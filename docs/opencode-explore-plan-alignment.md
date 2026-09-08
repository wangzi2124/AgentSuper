# opencode 探索(explore)/规划(plan) 设计对齐执行计划

> 依据 opencode 源码 `E:\project\opencode-dev`（对应线上 opencode monorepo）调研整理。
> 本次执行目标：把 AgentSuper 后端的 explore/plan 从「提示词约束 + 单次路由 + 无产物」对齐到 opencode 的「权限层裁剪 + 计划文件产物 + 可路由触发」。

## 一、opencode 设计调研结论

### 1. 角色定位与触发（`packages/opencode/src/agent/agent.ts`）

- **build**：默认主 Agent（primary），拥有全部工具 + `question`/`plan_enter`（agent.ts:140-155）。
- **plan**：另一个 **primary 模式**（agent.ts:156-181），权限上禁止所有 edit/write 工具，
  仅放行计划文件路径：`.opencode/plans/*.md` 与 `data/plans/*.md`；禁掉 `task`（不能并行委派）。
  用户请求复杂时，build 调 `plan_enter` 工具征询是否切到 plan（`src/tool/plan-enter.txt`）。
  计划完成后 plan 调 `plan_exit`（`src/tool/plan.ts`）征询是否切回 build 执行。
- **explore**：**subagent 类型**（agent.ts:196-218），权限 `"*": deny` + 只读 allowlist
  （grep/glob/list/bash/webfetch/websearch/read）。由主 Agent 通过 `task` 工具按需调用，
  可一回合并行多个（plan-mode 阶段一明确「并行拉起 ≤3 个 explore」）。
- **general**：另一个 subagent（agent.ts:182-195），负责「设计/执行多步研究」，plan 阶段二使用。

### 2. 只读/规划的**结构强制**（与 AgentSuper 最本质差异）

- opencode 在**工具注册层**按 agent 的 `permission ruleset` 裁剪可用工具；
  explore 只拿到只读工具，**写工具根本不会出现在 LLM 的 tools 列表里**，提示词只是辅助。
- plan 的模式约束同样是权限规则（edit/write 除计划文件外全 deny），不是靠 system prompt 自持。

### 3. plan-mode 工作流（`src/session/prompt/plan-mode.txt` + `src/session/reminders.ts`）

多阶段：
1. **理解阶段**：主 Agent 只派 explore subagent（并行 ≤3）读代码 + `question` 工具澄清需求。
2. **设计阶段**：派 general subagent 产出实现方案。
3. **评审阶段**：主 Agent 亲自读关键文件，用 `question` 收尾。
4. **成稿阶段**：把最终计划**写入计划文件**（会话内唯一路径，`Session.plan()`）。
5. **退出**：调 `plan_exit`，用户确认后注入一条「切到 build 执行计划」的 user 消息。

- 提醒注入：`reminders.ts` 按角色切换自动给 user 消息追加 `plan.txt`（进入 plan 的只读约束提醒）
  或 `build-switch.txt`（从 plan 切回 build 后提示「执行计划文件」），无需 LLM 自觉。
- **计划是跨轮次/跨会话共享的落盘产物**（`data/plans/*.md`），build 之后据此执行。

## 二、AgentSuper 现状差距（本仓库实测）

- `supermod/base.py:49` `ROUTABLE_AGENTS = {"build", "explore", "plan"}`（AGENTS.md 里的
  `{rag, web_search, code}` 已过期）。
- explore/plan 为 AgentBus 常驻对等 Agent（`runtime.py:179-183`）；
  前端 `agent_mode`（`stores/multiAgent.ts:92`）为 plan/explore 时直连对应 Agent
  （`chatmod/endpoints.py:140-143`），否则走 supervisor 关键词分解。
- **explore 只读只是提示词约束**：`explore_agent.py` 调 `sub_tools.tool_loop_chat`，
  工具集 `_TOOL_SCHEMAS`（`sub_tools.py:114-274`）含 write/append/edit/delete/rename/execute/apply_patch 全套，
  与 build 无异 → 模型不自律就可能改文件。
- **plan 无产物、无触发路径**：`plan_agent.py` 纯单次 LLM 调用，计划只内联回聊天；
  `supermod/decompose.py:49-79` 快速路径只返回 `[explore]` 或 `[build]`，`_llm_decompose` 无调用方（死代码），
  plan Agent 只能靠前端强选才可达。

## 三、落地实现（规则集化重设计，非补丁式强制）

> 初版按 T1–T5 逐项补丁（`tool_loop_chat` 加 `readonly` 布尔、plan 落盘、plan 关键词路由）
> 已按「重新设计」要求重构为**声明式规则驱动**：工具 allowlist 由集中的规格注册表声明，
> 不再在调用点散落 `readonly` 特判；supervisor 补齐 plan→build 顺序交接。

### D1 声明式 Agent 规格注册表（对齐 opencode `agent.ts` Info + permission ruleset）
- 新文件 `backend/app/agent/agent_specs.py`：`AgentSpec`（name/mode/tools/extended_timeout）
  集中声明 build/explore/plan 三个 Agent。
  - `build`：mode=primary，`tools=None`（自身 graph 工具，不裁剪），`extended_timeout=True`
  - `explore`：mode=subagent，`tools=_READONLY_TOOL_NAMES`（ls/read/glob/grep）
  - `plan`：mode=subagent，`tools=()`（纯 LLM，无工具）
- 让 Agent 拥有哪套工具 = 改这份注册表，属于可声明、可测试的规则。

### D2 工具 allowlist 驱动（对齐 opencode 工具注册层裁剪）
- `backend/app/agent/sub_tools.py`：`tool_loop_chat(readonly: bool)` 改为
  `tool_loop_chat(allowlist: tuple|None)` —— None=全量，元组=只暴露这些工具。
  - schema 层：`_tool_schemas(allowlist)` 裁剪，非白名单工具不出现在 LLM 的 tools 列表；
  - 运行时层：`_exec_one` 对非白名单工具名硬拒绝（规则 `"*": deny` 兜底，绕过也执行不了）。
- `backend/app/agent/explore_agent.py`：`allowlist=get_agent_spec("explore").tools`，
  只读约束由规则驱动而非提示词自觉。

### D3 规划产物落盘（对齐 opencode plan 文件）
- `backend/app/agent/plan_agent.py`：生成计划写入 `<data>/plans/<conversation_id>/plan.md`
  （`storage.paths.global_paths()["data"]`），响应 payload 与回复尾部带 `plan_path`。

### D4 plan→build 顺序交接（对齐 opencode `build-switch` 语义，无审批版）
- `supermod/base.py`：`_PLAN_HANDOFF_KEYWORDS` + `_should_handoff_to_build(question)`
  —— 命中「先规划再执行/按计划执行/plan and then execute…」等执行意图才交接。
- `supermod/core.py`：
  - `_collect_route()`：收集 `_route_to` 的唯一回复，供顺序交接复用；
  - `_route_plan_then_build()`：plan 产出计划 → 把计划文本（+plan_path）合成为 build 的
    user 消息 → build 执行 → 合并为单条回复（`## 实施计划` + `## 执行结果`）；
    build 失败时仍保留计划文本并透传错误（answer 携带完整计划）。
  - 保持与顶层 `send_and_wait` 的「单回复」契约：流式/非流式、落库都不需要改动。
- `supermod/base.py`：`_extended_timeout_agents` 由 `iter_agent_specs()` 中
  `extended_timeout=True` 的规格驱动（默认 build）+ env 扩展名单纯合并。

### D5 验证与测试
- 新增 `backend/tests/test_agent_specs.py`（规格表语义、explore 只读 allowlist、plan 无工具）。
- 增补 `backend/tests/test_sub_tools.py`（allowlist schema 裁剪 / 空 allowlist / 运行时硬拒绝 /
  默认全量）。
- 增补 `backend/tests/test_new_agents.py`（explore 从规格表取 allowlist）。
- 增补 `backend/tests/test_supermod_extra.py`（handoff 判定、plan→build 合并回复、
  无执行意图不交接、build 失败保留计划、plan 失败透传）。

## 四、明确不做（本次）/ 后续项
- 不引入 opencode 的 `question` 澄清工具与 `plan_enter/plan_exit` 模式切换（需前后端新增 SSE 交互）。
- 不让 plan Agent 先派 explore 做只读调研（保持纯 LLM，可由 supervisor 组合 explore→plan 扩展）。
- plan→build 交接目前是**无审批自动执行**（用户问题命中执行意图即触发）；如需门禁，
  后续可挂 opencode `question` 工具在 plan 产出后征询用户确认再执行。