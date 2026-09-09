# opencode 对齐状态总览（A/B/C 三波 + 配置护栏）

> 生成日期：2026-09-09
> 依据：docs 下三份对齐设计文档 + 后端代码逐条核实（行号为当日核实值）。
> 用途：把分散在三份文档 + config.py 注释里的 opencode 对齐工作合成一张总览，
> 回答「我们到底对齐了 opencode 的哪些东西、做到什么程度、刻意没做什么」。

## 0. 一句话结论

三波对齐（A 架构 / B explore-plan 规则化 / C tool_task 四缺口）**均已落地并经代码核实**；
配置层另有一批系统性对齐 opencode 语义的护栏（直接写在 config.py 注释，非文档流）。
整体**无「文档有、代码无」的悬空功能**；「明确不做」项与 opencode 刻意保持差异且前后一致。

## 1. 全景矩阵

| 波 | 对齐文档 | 对齐对象（opencode 源码） | 状态 | 关键落地文件 |
|---|---|---|---|---|
| A 架构 | `多智能体架构对齐opencode设计.md`（含 `多智能体设计差异执行.md` D1-D5） | `agent.ts` 7 内置 agent | ✅ 已完成 | explore_agent / plan_agent / agent_specs / supermod / ChatInput.vue / helpers.py / persist.py |
| B explore-plan 规则化 | `opencode-explore-plan-alignment.md` | `agent.ts` Info + permission ruleset + `data/plans/*.md` + build-switch | ✅ 已完成 | agent_specs.py / sub_tools.py / plan_agent.py / supermod base+core |
| C tool_task 四缺口 | `tool_task与opencode差异执行.md`（已补「八、执行结果 ✅」） | `tool/task.ts`（371 行 dev） | ✅ 已完成 | config.py / task_permission.py / task_registry.py / tools.py / generate.py / bus.py / task_bridge.py / endpoints.py |
| 护栏 | 无独立文档（config.py 注释内声明） | `overflow.ts` / `tail_turns` / `prompt.ts` / `max-steps.ts` / `external_directory` / 按 token 截断 | ✅ 已完成 | config.py:67-199 |

## 2. A 波：Agent 架构对齐（opencode 7 个内置 agent → AgentSuper 4 个）

| opencode agent | AgentSuper 现状 | 证据 |
|---|---|---|
| `build`（primary） | rag+code+web_search 已并入 build（RAGAgentWrapper 共享 RAGAgent 单实例），mode=primary，tools=None（自身 graph 工具不裁剪） | `agent_specs.py`；`runtime.py`（与 explore/plan/supervisor 共注册 4 个） |
| `plan` | 新增 PlanAgent：subagent、tools=()（纯 LLM，temperature 0.3 / max_tokens 2048 / history ≤8 条）；产物落盘 `data/plans/<conv>/plan.md` | `plan_agent.py` |
| `general` | 不单独成 agent；等效能力 = 主 Agent 的 `task_bus` + `tool_task` 委派（委派子集仅 explore/plan，subagent_depth 限制防嵌套） | `graphmod/tools.py` `_tool_task` |
| `explore` | 新增 ExploreAgent：subagent、只读 allowlist（ls/read/glob/grep），运行时硬拒绝写工具 | `explore_agent.py` + `sub_tools.py`（见 B 波） |
| `compaction` | 已有压缩机制（对齐 tail_turns/preserve_recent_tokens，见 §5） | `config.py:83-86` |
| `title` | 规则截取 + **LLM 生成**（首条消息先落规则标题，后台异步 LLM 覆盖，失败回退）——D3 原为「设计已定代码未实现」，已按 `多智能体设计差异执行.md` 补齐 | `chatmod/helpers.py` `_generate_title_llm`；`persist.py` `_refresh_title_llm` |
| `summary` | 明确不做（可选） | — |

**差异处置**：A 波反查出的 5 处差异（D1-D5）已由 `docs/多智能体设计差异执行.md` 逐项处置完成
（D1/D2/D4/D5 文档更新；D3 代码补齐 LLM 标题；D5 澄清 ChatInput.vue + `agent_mode`）。

**验证**：`agent_bus.list_agents()` = build / explore / plan / supervisor 四个；
旧 rag/web_search/code 的 agent_id 仅在 `stream_events.py` 保留作历史回放兼容。

## 3. B 波：explore/plan 规则化（对齐 opencode「工具注册层裁剪 + 计划文件产物」）

> 本波把 explore/plan 从「提示词约束 + 单次路由 + 无产物」升级为声明式规则驱动。

| 项 | opencode 语义 | AgentSuper 落地 | 证据 |
|---|---|---|---|
| Agent 规格注册表 | `agent.ts` Info（mode/tools/permission） | `AgentSpec`（name/mode/tools/extended_timeout）集中声明：build=primary/tools=None、explore=subagent/只读 4 工具、plan=subagent/tools=() | `agent_specs.py`（D1） |
| 工具 allowlist | explore permission `"*": deny` + 只读 allowlist，写工具不出现在 LLM tools 列表 | `tool_loop_chat(allowlist)`：schema 层 `_tool_schemas` 裁剪（非白名单不在 tools 列表）+ 运行时层 `_exec_one` 硬拒绝（绕过也执行不了，返回 not allowed）；explore 的 allowlist 取自规格表 | `sub_tools.py` + `explore_agent.py`（D2） |
| 规划产物落盘 | plan 写入 `.opencode/plans/*.md`、`data/plans/*.md` | 计划写入 `data/plans/<安全化 conversation_id>/plan.md`（防目录穿越），响应带 plan_path | `plan_agent.py`（D3） |
| plan→build 顺序交接 | `build-switch`（plan_exit 征询后注入执行计划消息） | 无审批自动版：`_PLAN_HANDOFF_KEYWORDS` + `_should_handoff_to_build` 命中执行意图 → `_route_plan_then_build()` 产出计划并把计划文本合成为 build 的 user 消息执行，合并单条回复（`## 实施计划` + `## 执行结果`），build 失败仍保留计划并透传错误 | `supermod/base.py` + `supermod/core.py`（D4） |

**B 波测试**：`test_agent_specs.py` / `test_sub_tools.py`（allowlist 裁剪 + 硬拒绝）/
`test_new_agents.py`（explore 取规格 allowlist）/ `test_supermod_extra.py`（handoff 判定、合并回复、失败透传）。

## 4. C 波：tool_task 四缺口（对齐 `tool/task.ts` 371 行 dev 版）

> 设计文档 `tool_task与opencode差异执行.md` 已补「八、执行结果 ✅」，以下为速览。

### ① permission.task 声明式授权
- 配置源：`config.py:199` `task_permission_rules: dict = {}`（空 = 全 allow，零迁移）。
- 规则求值：`graphmod/task_permission.py` `resolve()` 按顺序 glob 匹配、**最后胜出**；`allowed_subagent_types()`。
- schema 裁剪：`graphmod/base.py` 挂载 tool_task 前按规则过滤 enum —— **deny 的 subagent 模型根本看不到**（对齐 opencode description 移除语义）。
- 运行时：`graphmod/tools.py:127` resolve → deny 直接拒绝（返回 disabled 提示）；ask 走 `permission_request → await_decision` 审批桥（tools.py:141-156，无事件队列即拒绝不阻塞，tools.py:154）。

### ② task_id resume 子会话复用
- `tools.py:121` 读取 `task_id` → `:160` 稳定 thread（`task_id or f"task:{uuid8}"`）→ `:161` 命中 registry 时以 `get_history(task_id)` 续跑（对齐 `task_id ?? create`）。
- 返回带 `<task id=... state="completed|error">` 包装，模型可解析 id 回传续跑。
- `graphmod/task_registry.py` `TaskRegistry`：record / get_history / get / peek_last_answer / record_ids / push_background_result / drain_background_results（进程内，重启即失——与 opencode 持久 session 的刻意差异，见 §6）。

### ③ background 异步委派
- `tools.py:88` `_background_tasks: dict[thread_id, Task]` 模块级表；`:196-206` `asyncio.create_task` **立即返回** `<task state="running">` 包装，完成后 `push_background_result`。
- 父 Agent 吸收：`generate.py:291-293` 每轮 LLM 调用前 `drain_background_results(conversation_id)` **前置注入**合成 assistant 消息（对齐 `injectBackgroundResult`）——本轮无 tool_calls 也能收尾。

### ④ abort 级联取消（实现比设计文档多出工作侧中断）
- `bus.py:216` `cancel_pending`（停等待方）＋ `:228` `abort_work`（**取消在途 handler task**）＋ `:241-251` `abort(thread_id)` 双通道计数返回。
- `bus.py:285-294`：每条消息实现在独立 handler task 中运行，供 `abort_work` 精确中断；被单独取消的 handler 吞掉 CancelledError 继续处理下一条。
- `task_bridge.py` `cancel/cancel_children` 升级为经 `bus.abort` 级联；`endpoints.py` 取消路径 `:166-173`（HTTP 499）、`:374-382`、`:398-412`（排队/信号量期取消）均置会话 `interrupted` 并登记清理。

**C 波测试**：`test_task_permission.py`（2970B）+ `test_task_registry.py`（2802B），2026-09-09 14:34 新增。

## 5. 配置护栏（config.py：系统性对齐 opencode 运行时语义）

> 这批护栏没有独立设计文档，直接以注释写在 `config.py` 里并标注对齐对象（与代码同源维护，不怕文档漂移）。
> 覆盖四族：token 预算/压缩、执行循环、权限、委派/记忆。行号为 2026-09-09 核实值。

### 5.1 Token 预算与压缩（对齐 `overflow.ts` + `tail_turns` + 按 token 截断）

| 配置项 | 值 | 对齐对象/语义 | 行号 |
|---|---|---|---|
| `max_context_tokens` | 24_000 | `overflow.ts`：usable = max − context_reserve（v5 48K→32K，v9 32K→24K） | :70 |
| `context_reserve_tokens` | 8_192 | 输出预留 ≈ min(20_000, maxOutputTokens) | :72 |
| `compaction_threshold_ratio` | 0.6 | 压缩触发比例 = usable × ratio（v9 0.65→0.6，「压缩早于截断」） | :80 |
| `context_tail_turns` | 2 | 对齐 `tail_turns`：压缩时尾部保留最近轮次 | :84 |
| `context_preserve_recent_tokens` | 8_000 | 对齐 `preserve_recent_tokens`：尾部保留 token 预算 | :86 |
| `token_estimate_correction` | 1.13 | cl100k_base 对 DeepSeek tokenizer 系统性低估 +13.2% 的估算校正（实测 round8/9） | :76 |
| `tool_output_max_tokens` | 8_000 | 对齐 opencode「按 token 截断」：单条工具输出超限落盘 + 续读提示 | :104 |
| `context_safety_ratio` / `compaction_target_ratio` | 0.9 / 0.5 | 截断/压缩目标留 ≥10% 估算余量；压缩后余量充足（C5） | :97/:100 |
| `tool_output_protect_tokens` / `tool_output_prune_minimum_tokens` | 24_000 / 12_000 | 回溯式工具输出清理；实际生效值由 budget.py 与压缩阈值联动钳制 | :91/:93 |

### 5.2 执行循环护栏（对齐 `prompt.ts` / `processor.ts` / `max-steps.ts`）

| 配置项 | 值 | 对齐对象/语义 | 行号 |
|---|---|---|---|
| `max_steps` | 24 | 对齐 `agent.steps`（默认 40，本地调小）；末轮注入收尾提示并禁工具 | :181 |
| `max_tool_rounds` | 8 | 单请求最多 LLM 轮数（v9 16→8）；生效上限 = min(max_steps, max_tool_rounds) | :185 |
| `doom_loop_threshold` | 3 | 同指纹工具调用连续 N 轮注入策略提示（≥2） | :187 |
| `doom_loop_max_strikes` | 2 | 二次触发即强制收尾，对齐 `processor.ts` ask(doom_loop)→deny→stop | :190 |
| `sub_agent_timeout_extended` | 300.0 | 工具密集型子 Agent 长等待超时 | :192 |
| `extended_timeout_agents` | "code" | 默认名单为历史 agent 名；实际生效以 `agent_specs.py` 各 agent 的 `extended_timeout` 声明为准（build=True） | :194 |
| `subagent_depth` | 1 | 对齐 opencode `subagent_depth`：主 Agent 只能再委派一层，防嵌套爆炸 | :196 |

### 5.3 权限护栏（对齐 `external_directory` + `permission.task`）

| 配置项 | 值 | 对齐对象/语义 | 行号 |
|---|---|---|---|
| `external_path_default` | "ask" | 对齐 `external_directory` 设计：工作区外路径默认 ask | allow | deny（可写工作目录由前端配置、运行时持久化 `data/runtime_workspaces.json`） | :166-169 |
| `permission_approval_timeout` | 60 | 审批等待超时（秒），超时视为拒绝 | :171 |
| `allow_source_writes` | False | 受保护源码路径（app/plugins/skills/config/main.py）硬保护；.git/.env/*.db/permissions.json 恒保护 | :172-176 |
| `task_permission_rules` | {} | 对齐 `permission.task`（C 波 ①）；空 = 白名单全 allow，零迁移 | :197-199 |

### 5.4 其它运行护栏（委派 / 记忆 / 并发）

| 配置项 | 值 | 语义 | 行号 |
|---|---|---|---|
| `sub_task_fresh_history` | True | v15：并行分解子任务用 fresh context（防 N 个子 Agent 各自 prefill ≤16K 历史） | :203 |
| `memory_persist_path` / `memory_ttl_seconds` | data/agent_memory.json / 300 | 共享记忆落盘 + 5 分钟 TTL | :207/:209 |
| `max_concurrent_agents` | 4 | 全局 Agent 任务信号量（SQLite/ChromaDB 锁竞争权衡） | :155 |
| `llm_max_tokens` | 8_192 | 对齐 `transform.ts:maxOutputTokens`「默认给足」（v9 16K→8K 压低兜底成本） | :63 |

## 6. 刻意差异 / 明确不做（opencode 有而我们不同——均为有意取舍，非缺口）

| # | 维度 | opencode | AgentSuper 选择 | 性质 |
|---|---|---|---|---|
| K1 | Agent 阵容 | 7 内置：build/plan/general/explore/compaction/title/summary | 注册 4 个：build/explore/plan/supervisor。rag/web_search/code 能力已并入 build；general 的能力由主 Agent 的 `task_bus` + `tool_task` 委派承担；compaction/title 为内置机制而非独立 agent | 收敛简化（D1） |
| K2 | 模式切换 | `question`/`plan_enter`/`plan_exit` 显式模式流 | 不做。以 supervisor 自动路由（关键词快速路径 + `_llm_decompose`）+ 请求级 `agent_mode: plan\|explore` 直连替代 | 明确不做 |
| K3 | plan→build 交接 | `build-switch`：plan_exit 后**征询用户**再执行 | 无审批自动版：`_PLAN_HANDOFF_KEYWORDS` + `_should_handoff_to_build` 命中执行意图 → 自动产出计划并合成执行消息，合并单条回复（`## 实施计划` + `## 执行结果`），失败仍保留计划并透传错误 | 自动化增强（D4） |
| K4 | plan 是否先派 explore | plan 先 explore 收集上下文 | 不做。plan 纯 LLM（温度 0.3 / ≤8 条历史），上下文由 supervisor 分解或问题自带 | 明确不做 |
| K5 | task 生命周期 | 持久 session | 进程内 `TaskRegistry`（重启即失）；resume 依赖当前进程 registry | 刻意差异（C②） |
| K6 | summary agent | 可选内置 | 明确不做 | 明确不做 |
| K7 | 步数/超时默认值 | agent.steps 默认 40 | max_steps 24 + max_tool_rounds 8（本地实测 token/延迟调小，语义对齐） | 调参非缺功能 |
| K8 | 中断能力 | abort 停等待方 | `bus.abort` 双通道：cancel_pending（停等待）**+ abort_work（取消在途 handler task）**，比设计文档多出工作侧中断 | 增强（C④） |
| K9 | extended_timeout_agents 默认值 | —（agent 声明式） | config 默认 "code" 为历史 agent 名残留；生效名单由 agent_specs 的 `extended_timeout` 声明决定（build=True） | 小残留，非功能缺口 |

**差异总判**：上表无一项是「opencode 有能力而 AgentSuper 丢失」；K2/K4/K6 为有意不做（保持自动路由的简洁），K3/K8 反而是超出原版的能力增强。
config.py 注释族（§5）与设计文档族（§2-§4）共同构成 opencode 对齐的**双层证据**：语义层对齐 + 默认值按本地实测调参。

## 附：证据文件清单（对照查阅）

| 文件 | 内容 |
|---|---|
| `backend/app/config.py` | §5 全部护栏（:60-:218） |
| `backend/app/agent/agent_specs.py` | build/explore/plan 规格（mode/tools/extended_timeout） |
| `backend/app/agent/supermod/base.py` + `core.py` | ROUTABLE_AGENTS 白名单、plan→build 交接（K3） |
| `backend/app/agent/supermod/decompose.py` + `parallel.py` | 意图路由 + asyncio.gather 并行（K2 替代实现） |
| `backend/app/agent/sub_tools.py` + `explore_agent.py` | allowlist 裁剪 + 运行时硬拒绝（B 波） |
| `backend/app/agent/bus.py` + `task_bridge.py` | send_and_wait / abort 双通道（K8） |
| `backend/app/graphmod/task_permission.py` + `tools.py` + `generate.py` | permission.task / task_id resume / background 注入（C 波） |
| `backend/app/session/agent_executor.py` + `api/chatmod/endpoints.py` | 事件落库桥接 + SSE / multi-agent 端点 |
| `frontend/src/stores/multiAgent.ts` + `views/MultiAgentView.vue` + `api/multiAgent.ts` | 前端并行面板 / SSE 消费 / agent_mode |

> 备注：§2 行 32 title 行——D3 处置后为「规则首落 + 后台 LLM 覆盖，失败回退」，见 `多智能体设计差异执行.md`。
> 本总览 2026-09-09 生成；若后续代码继续演进，建议以 config.py 注释（§5 来源）为最新基准。
