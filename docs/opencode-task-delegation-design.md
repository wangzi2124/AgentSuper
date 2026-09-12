# opencode 子 Agent 自动委派（task → explore）对齐设计

> 调研依据：`E:\project\opencode-dev`（packages/opencode、packages/core、packages/tui、packages/opencode/src/agent）。
> 本文回答「为什么 opencode 是「前端只有 build/plan，探索由主 Agent 自动委派多个 task 执行」，
> 而 AgentSuper 是「前端手动选 explore/plan agentMode + supervisor 关键词路由」——**差在哪**，以及怎么对齐。
> 仅设计文档，不包含实施；批准后按本文分阶段执行。

## 一、opencode 的真实机制（源码级）

### 1. 前端只有两个 primary Agent，explore 是 subagent

- 用户可选的 Agent = **`mode !== "subagent" && !hidden`**（agents 列表过滤条件，`packages/tui/src/context/local.tsx:78`、
  `packages/opencode/src/cli/cmd/run.ts:610`、`stream.transport.ts:548`）。
  build/plan 是 `primary`（`packages/opencode/src/agent/agent.ts:141-181`），是**唯一** UI 可见模式。
- `explore`/`general` 是 `mode: "subagent"`（`agent.ts:182-218`），**用户在前端永远选不到**，
  只能由主 Agent 通过 `task` 工具**自动 spawn**。

### 2. 委派工具 `task`（`packages/opencode/src/tool/task.ts` + `registry.ts`）

- `task` 注册进全局工具注册表（`registry.ts:230-247`），并**出现在 build 的系统提示词里**
  （`default.txt:81`：「When doing file search, prefer to use the Task tool… reduce context」）。
- 输入 schema（`task.ts:43-62`）：`{description, prompt, subagent_type, task_id?, command?}`。
- 执行（`task.ts:92-213`）：
  1. 深度守卫 `subagent_depth`（默认 1，`task.ts:104-117`，沿 `parentID` 链走，超出报错「Subagent depth limit」）；
  2. 权限询问 `ctx.ask({permission:"task"})`（`task.ts:119-129`），子 Agent 权限 = 父权限 + 裁剪；
  3. **spawn 子会话**：`sessions.create({parentID: ctx.sessionID, agent: next.name, permission: childPermission})`
     （`task.ts:136-172`）——子会话是**独立 session 行（parent_id 链接）**，不是内存临时对象；
  4. 子会话跑**完整独立的工具循环**：`ops.prompt({sessionID: child, agent: next.name})`（`task.ts:200-213`），
     子 Agent 用自己的 model（无则继承父）、自己的权限规则集、自己的消息上下文；
  5. 结果回灌：父 Agent 只拿到 child 的**最后一段文本**作为 tool result（`task.ts:213`
     `result.parts.findLast(p => p.type === "text")?.text ?? ""`），包进 `<task>…</task>` 信封。

### 3. plan 模式下自动并行 explore（`packages/opencode/src/session/prompt/plan-mode.txt`）

- 「Phase 1: Initial Understanding —— Launch up to 3 explore agents IN PARALLEL, single message, multiple tool calls，
  minimum number of agents necessary (usually just 1)」。
- 意思是：plan 模式下，主 Agent（plan primary）在**同一条消息里并行发起多个 `task(subagent_type="explore")`**，
  每个 explore 只读探索一块，结果回来后聚合进计划。
- plan Agent **没有权限 spawn build/explore 之外的 agent**（plan 的 ruleset deny 所有 edit 工具、
  `task: { general: "deny" }`，只能委派 explore——`agent.ts:156-176`）。

### 4. 总结：opencode 的核心是「**委派是主 Agent 的一个真实、LLM 可调、可并行、可 spawn 子会话的工具**」

| 维度 | opencode |
|---|---|
| 工具注册 | `task` 在 `task.ts` 真实现 + 注册进 registry + 出现在 build 提示词 |
| 子会话 | `sessions.create(parentID=…)` 真建 player_session 行，独立上下文/token/成本 |
| 权限隔离 | 子 Agent 独立 ruleset（explore 只读 allowlist，`agent.ts:196-218`） |
| 并行 | plan 阶段一「并行 explore ≤3」；build 提示词鼓励 task 降 context |
| 触发者 | **模型自主**（工具循环内由 LLM 调 task），非前端选模式 |
| 深度守卫 | `parentID` 链 + `subagent_depth`（默认 1） |
| 结果回灌 | 仅 child 最后一段文本作为 tool result，包 `<task>` 信封 |

## 二、AgentSuper 现状差距（源码级）

| 维度 | AgentSuper（现状） | 差距 |
|---|---|---|
| 工具注册 | `tool_task` **只存在于** `backend/app/agent/tools.py:268` 的系统提示词文本里
  （"tool_task(description, prompt, subagent_type) - Delegate …"），
  **没有进 `_TOOL_SCHEMAS`**（`backend/app/agent/sub_tools.py:150` 注册表无 task 条目） | **LLM 看不到、列表无、调不到** —— 只是死文本 |
| 委派执行 | `tool_loop_chat`（`sub_tools.py:365`）只做**单一会话内的工具 loop**，
  **不 spawn 子会话**（无 parent_id/child session 创建） | 无真正的「子 Agent」运行时 |
| 权限隔离 | explore 只读 allowlist 已存在（`agent_specs.py` `_READONLY_TOOL_NAMES` + schema 裁剪 + 运行时硬拒绝） | ✅ 已对齐，无需改 |
| 并行 | `supermod/parallel.py` 有 `_execute_parallel`（build/explore/plan 三个 Agent 并行） | 已有基建，但非「模型自主多 task」 |
| 触发者 | **前端手动选** `agentMode`（`multiAgent.ts:92`）+ supervisor 关键词路由 decompose（`supermod/decompose.py`） | **模型不自主** —— 是关键词规则改谁进 explore，不是模型决定 |
| 子会话基建 | `sessions.parent_id` 列 + `parent_id IS NULL` 根过滤已存在（`session/repository.py:174,224-240`） | ✅ 表结构已就位，缺「写」的运行时 |

**一句话**：opencode 把「探索」做成主 Agent **模型自主调用的委派工具**；我们把「探索」做成**前端手选的模式 + 关键词路由**。
表里的 `parent_id` / 只读 allowlist / 并行基建我们其实**都有**，缺的是——

1. `task` 工具真正注册成 LLM 可调（schema）+ 真 handler；
2. handler **spawn 子会话执行**（复用 `parent_id` + `AgentBus`），子结果回灌成 `<task>` 信封；
3. plan 模式/探索意图下「并行多 explore」作为提示词激励（对齐 plan-mode.txt）；
4. 由模型自主触发（build 主循环里提示词引导调 task），而非前端模式选择。

## 三、设计（对齐 opencode 的落地方案）

### D1 把 task 注册成真工具（对齐 `task.ts` schema）

`backend/app/agent/sub_tools.py` `_TOOL_SCHEMAS` 增加（**对齐 registry.ts 任务工具 schema**）：

```python
{
  "type": "function",
  "function": {
    "name": "tool_task",
    "description": (
      "Delegate a focused, independent subtask to a sub-agent (subagent_type: 'explore' for read-only "
      "codebase exploration, 'general' for multi-step research) and get its final result back. "
      "The sub-agent runs in its own session with a separate permission ruleset — use it for "
      "specialized or parallel work; do NOT delegate what you can do directly. "
      "Launch multiple sub-agents concurrently whenever possible."
    ),
    "parameters": {
      "type": "object",
      "properties": {
        "description": {"type": "string", "description": "Short (<10 words) description of the subtask"},
        "prompt": {"type": "string", "description": "The task for the sub-agent to perform, as a user message"},
        "subagent_type": {"type": "string", "enum": ["explore", "general"], "description": "Which agent to spawn"},
      },
      "required": ["description", "prompt", "subagent_type"],
    },
  },
}
```

- `subagent_type` → 查 `agent_specs.py` 的 AgentSpec 注册表得到子 Agent 的 **tool allowlist**
  （explore = `_READONLY_TOOL_NAMES`），子 Agent 只在裁剪后的 schema 上跑 `tool_loop_chat`（复用现有 allowlist 机制）。
- build/explore/plan 的 allowlist 里显式加上 `tool_task`（build 可委派 explore/general；plan 只能委派 explore，
  通过 allowlist 收窄；explore 只读 → **无权委派**，allowlist 不包含 task）。

### D2 真 handler：spawn 子会话执行（对齐 `task.ts:136-213`）

新增 `backend/app/agent/sub_tools.py` 的 `_exec_one` 分支（或独立 `tool_task` handler）：

```text
1. 读取当前会话（ctx.sessionID）→ parent session；
2. depth 守卫：沿 parent_id 链数层数，≥ MAX_SUBAGENT_DEPTH（默认 1）→ 返回错误文本「Subagent depth limit reached」；
3. 解析 subagent_type → AgentSpec（agent_specs.py）→ tool allowlist + model + 昵称；
4. 用 AgentBus 创建子 AgentRequest（含 subagent_type、prompt、parent_id=当前会话、root=父 directory）；
   —— 复用现有 AgentBus spawn（backend/app/agent/bus.py），子 Agent 跑独立子会话（session.parent_id 已支持）；
5. 等待子 Agent 完成 → 取子会话最后一条消息文本（对齐 task.ts:213 findLast text）；
6. 返回 `"<task id=... state=completed><summary>...</summary>{text}</task>"` 信封给父 LLM。
```

产物落盘：子会话独立记录自己的 token/usage（对齐 opencode「子会话 token 独立核算」），
父会话只拿 `<task>` 文本，不复制子上下文（对齐 opencode「子会话上下独立，父只看 result」）。

### D3 委派由模型自主触发（对齐 opencode 的核心语义）

- **build 主循环提示词**加一段（对齐 default.txt:81 + task.txt）：
  ```
  当任务涉及大范围代码/多文件/未知区域时，优先用 tool_task 委派 explore 子 Agent 去只读探索，
  再基于结果继续 —— 这能减少主上下文占用。可同时并行委派多个 explore。
  ```
- **plan 模式激励（对齐 plan-mode.txt）**：plan primary 的提示词注入
  「阶段一：并行启动最多 3 个 explore 子 Agent 做只读理解，每行一条 tool_task 调用」。
- **前端不再需要手动 explore 模式**：`agentMode` 里 explore 从「用户可选」降为「仅供委派」，
  前端只留 build/plan（对齐 opencode 只有 build/plan 两个 primary）。explore 作为 subagent 仍可由用户
  @ 显式调用（对齐 opencode 的 task tool 可由用户命令触发），但**默认自动委派**。
- supervisor 关键词路由（explore/plan 意图）保留为**兜底**，主路径应为「模型自主 task」。

### D4 深度守卫 + 超时（对齐 task.ts:104-117 / processor.ts）

- `MAX_SUBAGENT_DEPTH` 默认 1（build → explore 一层），超链返回错误（防子 Agent 无限 spawn）。
- 子 Agent 超时沿用 `_extended_timeout_agents`（build 延长），explore/plan 保持短超时。
- 并行 spawn 复用 `_execute_parallel` 的并发控制 + `max_concurrent_agents`。

## 四、分阶段实施

1. **P0 注册表**：`_TOOL_SCHEMAS` 加 `tool_task` schema；`agent_specs.py` 的 allowlist 声明里
   build 加 task、plan 只 esplore、explore 无 task。带 pytest（断言 schema 出现 + allowlist 收窄）。
2. **P1 运行时**：`tool_task` handler（depth 守卫 + AgentBus spawn 子会话 + 取末文本 + `<task>` 信封）。
   带 pytest（spawn 成功/深度超限返回错误/explore 只读 verify）。
3. **P2 提示词**：build 委派引导 + plan 并行 explore 激励（对齐 plan-mode.txt）。
4. **P3 前端**：`agentMode` explore 降为 subagent（前端只留 build/plan）；SSE 子 Agent 事件透传。
5. **验证**：全量 pytest + `npm run check`；手动回归「大范围任务 → 主 Agent 自动调 task 委派 explore」。

## 五、不做（本次范围外）

- 不实现 opencode 的 `plan_enter/plan_exit` 模式切换（需审批门禁）；
- 不做跨会话上下文共享（子会话上下文独立，父仅见 `<task>` 文本）；
- 不引入 subagent 级 cost 表（沿用子会话独立 usage 落库）。
