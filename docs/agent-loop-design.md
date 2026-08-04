# AI 助手 Agent 执行循环与权限设计

> 参考 opencode 的 agent 执行循环设计，适配本项目的 **FastAPI + LangGraph + AgentBus + SQLite** 技术栈。
>
> ⚠️ **注意**：本文档为历史设计记录。其中 §3.1 的 `EXTRA_WORKSPACES` 环境变量方案已被
> [工作目录管理与文件系统工具设计](./workspace-and-filesystem-design.md) 取代——可写工作目录
> 现在唯一由前端「工作目录」面板配置（持久化于 `data/runtime_workspaces.json`）。
>
> opencode 核心设计（源码参考）：
> - 主循环：`packages/opencode/src/session/prompt.ts`（`runLoop`：finish 判定 → 任务解析 → 压缩任务 → 工具调用 → `compact/stop/continue`）
> - 单步处理器：`packages/opencode/src/session/processor.ts`（事件流 → part 持久化 → tool-call 管理 → doom-loop 检测）
> - 权限模型：`packages/opencode/src/agent/agent.ts`（`allow/ask/deny` + `external_directory`/`doom_loop` 默认规则，per-agent ruleset）
> - 步骤上限：`packages/opencode/src/agent/agent.ts`（`steps`）+ `packages/core/src/session/runner/max-steps.ts`（`MAX_STEPS_PROMPT`）
> - 重试策略：`packages/opencode/src/session/retry.ts`（backoff、`retry-after`、5xx 必重试、溢出不重试）
> - 执行协调：`packages/core/src/session/run-coordinator.ts`（per-session 串行，跨 session 并行，wake 合并）

---

## 1. 背景与现状

### 1.1 失败案例

用户请求：「在 D 盘 用 REACT 技术 写一个 俄罗斯方块游戏，保证可靠可用 尤其空格逻辑」。

多 agent 模式与普通模式均**未完成任务**。观察到的现象：

1. Agent 反复尝试把项目写到 `D:\` 失败。
2. 多 agent（AgentBus）路径下每次写盘立即得到 `Permission denied`；普通（SSE）路径下每次写盘阻塞约 120s 审批超时后同样失败。
3. 工具轮次在权限错误上被耗尽，Agent 最终放弃或只交付半成品。

### 1.2 现状代码定位

| 位置 | 职责 | 与本案例相关的问题 |
| --- | --- | --- |
| `backend/app/tools/filesystem.py:14` | `WORKSPACE = backend/` 硬编码 | 工作区唯一，无扩展点 |
| `backend/app/tools/filesystem.py:36-53` | `_resolve` / `_ensure_safe` | `D:\` 路径 → `PermissionManager.check` → `ask` → `NeedsPermission` |
| `backend/app/permission/manager.py:98-186` | `classify_path` / `check` | `external` 分类一律 `ask`；`temp` 放行；无"扩展工作区"概念 |
| `backend/app/agent/graph.py:164-215` | `_execute_tool` 权限审批 | 无事件队列（总线路径）时**直接拒绝**；有队列时 `await_decision(timeout=120)` 阻塞 120s |
| `backend/app/agent/graph.py:217-236` | `_execute_tool_streaming` | 同一套外部路径 `ask` 限制 |

### 1.3 根因

1. **工作区沙箱锁定 `backend/`**：文件系统工具无法在 `D:\` 写任何文件，任务从工具能力上不可完成。
2. **无审批通道时直接拒绝**（`graph.py:195-199`）：多 agent 总线路径 `event_queue=None`，权限请求无人审批 → 立即拒绝，Agent 只能得到工具错误。
3. **有审批通道时无级联缓解**：`await_decision` 120s 超时每次请求都完整阻塞一次工具调用，多个外部路径写盘 = 数分钟级空转。
4. **循环缺乏护栏**：`MAX_TOOL_ROUNDS` 是唯一硬上限；无「步骤上限 + 收尾提示」、无 doom-loop 检测、无待办规划，长任务容易在错误路径上空耗。
5. **子代理固定超时**：supervisor 对子 agent `SUB_AGENT_TIMEOUT=150s` 硬等待，工具密集型子任务（脚手架、构建）容易超时被误判失败。

---

## 2. opencode 参考设计

### 2.1 主循环（`prompt.ts:runLoop`）

```
while True:
  msgs = filterCompacted(...)                 # 过滤已压缩历史
  lastUser / lastAssistant / tasks = latest(msgs)
  if lastAssistant.finish 且非 tool-calls 且无未完成工具调用:
      break                                   # ← 结束语义（finish 驱动）
  step++
  if tasks.pop() == "subtask":  handleSubtask(...); continue
  if tasks.pop() == "compaction": compaction.process(...); break if "stop"; continue
  if lastFinished 溢出: compaction.create(auto); continue
  maxSteps = agent.steps ?? Infinity
  handle = processor.create(assistantMessage)
  result = handle.process(messages=[..., (isLastStep ? MAX_STEPS_PROMPT : [])])
  if result == "stop": break
  if result == "compact": compaction.create(auto, overflow); continue
```

关键点：

- **以 `finish` 而非轮数为循环出口**；`tool-calls` finish 不算结束（`prompt.ts:1106-1130`）。
- **溢出→自动压缩→自动续跑** 由合成 user 消息驱动（`compaction.ts:485`）。
- **步骤上限注入提示词**：最后一轮注入 `MAX_STEPS_PROMPT`，强制"文字总结 + 剩余任务清单"，避免无意义继续（`prompt.ts:1178-1281`）。
- 子任务 / 压缩任务以 **task 队列**形式插入循环，与主线程串行复用同一套处理器。

### 2.2 单步处理器（`processor.ts`）

- 每个 assistant 消息一个 `Handle`，`process()` 消费 LLM 事件流：`text/step/tool-call/tool-result` → 写 part。
- **doom-loop 检测**（`processor.ts:29,331-381`）：`DOOM_LOOP_THRESHOLD=3`，同一工具相同输入连续出现 3 次 → 触发 `doom_loop` 权限询问（默认 `ask`），打破死循环。
- **重试**（`retry.ts`）：指数退避 `2s × 2^(n-1)`，优先 `retry-after` 头；5xx 一律重试；上下文溢出**不重试**（转压缩）。

### 2.3 权限模型（`agent.ts:108-136`）

```
defaults:
  "*": "allow"                    # 默认放行
  doom_loop: "ask"
  external_directory: {"*": "ask", 白名单目录: "allow"}
  read: {"*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow"}
```

- **按 agent 配置 ruleset**（`mode: primary/subagent/all`、`steps`、`permission`）。
- **目录分层**：工作区内默认 `allow`；`external_directory` 需要显式规则（skill/tmp/reference 目录默认 `allow`）。
- 权限拒绝 ≠ 工具报错：拒绝是可解释的决策事件，Agent 可据此调整路径（提示词中说明拒绝原因）。

### 2.4 执行协调（`run-coordinator.ts`）

- per-session 串行、跨 session 并行；新输入以 `wake` 合并到当前执行尾。
- 打断（interrupt）中断 owner fiber，后续输入排队。

---

## 3. 目标设计（适配 AgentSuper）

### 3.1 工作区与权限模型 v2

**配置化工作区**（`backend/.env`）：

```
# 允许 Agent 读写的额外工作区（逗号分隔，支持盘符/绝对路径）
EXTRA_WORKSPACES=D:\Projects\games,D:\temp
# 默认 external 路径策略：ask | allow | deny
EXTERNAL_PATH_DEFAULT=ask
# 审批等待超时（秒），默认 60
PERMISSION_APPROVAL_TIMEOUT=60
```

**`PermissionManager` 改造**：

1. 新增 `self.extra_workspaces: list[Path]`，`classify_path` 在 `workspace` 之前/之后先匹配 `extra_workspaces` → 归类 `workspace`（写/执行同规则）。
2. `check()` 对 `external` 返回 `EXTERNAL_PATH_DEFAULT`（默认 `ask`），不再硬编码 `ask`。
3. 审批流增加 **级联临时授权**：路径获得 `allowed` 后，`add_temp_approval` 同时登记父目录，同目录后续写入免审（已有实现，复用）。
4. 持久化白名单 `permissions.json` 保持兼容，`respond(remember=True)` 继续支持。

**文件系统工具**：

- `_resolve` 不变（相对路径基于 `WORKSPACE`）。
- `tool_write_file` / `tool_execute` 对 `extra_workspaces` 内路径放行（与 `workspace` 同策略）。

### 3.2 审批通道统一

- **总线路径不直接拒绝**：`_execute_tool` 无事件队列时，改为「有默认审批者则自动决策，否则短暂等待审批 + 超时拒绝」，并返回可解释错误（`Permission denied for <path> (<operation>). 可在 D:\... 内创建或配置 EXTRA_WORKSPACES 后重试`），让 LLM 可据此换路径。
- **审批请求异步化**：`permission_request` 事件照常入队，等待不阻塞整个事件循环（现有 `await_decision` 已为 async，可保留，但缩短默认超时并支持 `EXTERNAL_PATH_DEFAULT=allow` 时跳过审批）。

### 3.3 主循环：步骤上限 + 结束语义

在 `graph.py` 的 `_generate` 工具循环中引入 opencode 式护栏：

1. **`MAX_STEPS`**（配置化，默认 40）：超过后不再调用工具，注入 `MAX_STEPS_PROMPT`（复刻 `max-steps.ts` 文案：强制文字总结已完成/未完成/下一步），随后正常收尾。
2. **结束语义**：以"assistant 本轮无工具调用"为自然出口（现状已具备），`MAX_STEPS` 仅为护栏，不替代自然结束。
3. **doom-loop 检测**：工具循环内维护 `(tool, args)` 指纹队列，同一指纹连续 ≥3 次 → 停止重试，向 LLM 注入「检测到重复工具调用，请改变策略或直接作答」。

### 3.4 任务规划 / 待办（可选增强）

- 参考 opencode 的 todo 机制：长任务首轮让模型产出 `Todo[]`，随步骤更新（已完成/进行中/待办）。
- 落地最小方案：在系统提示词中要求多文件任务先输出「实施计划」文本块，LLM 在每轮引用进度；配合 3.3 的收尾提示词，交付「剩余任务清单」。

### 3.5 子代理执行与超时

- **超时不再一刀切**：对工具密集型子任务（脚手架/构建）放宽或支持心跳续期；区分「无输出」与「仍在运行」。
- **结果回传**：多 agent 子任务失败时，把子任务「已完成步骤 + 失败原因 + 建议」作为完整上下文回传 supervisor，而不是仅一个 `Agent 'x' did not respond in time` 错误。

### 3.6 重试策略对齐

- 采用 `retry.ts` 语义：指数退避 2s→4s→8s（现有 TaskRunner 已具备），对 5xx/429 必重试；**权限拒绝不重试**（重试无意义，属于策略性错误，应返回可解释信息让模型换路径）；上下文溢出走压缩而非重试。

---

## 4. 落地实施计划

| 阶段 | 改动 | 涉及文件 |
| --- | --- | --- |
| P1 工作区扩展 | 新增 `EXTRA_WORKSPACES` / `EXTERNAL_PATH_DEFAULT` / `PERMISSION_APPROVAL_TIMEOUT` 配置；`PermissionManager` 支持额外工作区 | `backend/app/config.py`、`backend/app/permission/manager.py`、`backend/app/tools/filesystem.py`、`backend/.env.example` |
| P2 审批通道 | 总线路径自动决策 + 可解释拒绝文案；缩短默认审批超时 | `backend/app/agent/graph.py:164-215` |
| P3 循环护栏 | `MAX_STEPS` + `MAX_STEPS_PROMPT` 注入；doom-loop 指纹检测（≥3 连续相同） | `backend/app/agent/graph.py`、`backend/app/context/tool_dedup.py` |
| P4 子代理超时 | 子任务心跳/超时分级，失败结果结构化回传 | `backend/app/agent/supervisor.py`、`backend/app/agent/bus.py` |
| P5 待办与收尾 | 多文件任务计划块 + 收尾清单提示词；文档更新 | `backend/app/agent/tools.py`（系统提示词）、`README.md` |

**验收标准**：

1. `D:\Projects\games`（或任一 `EXTRA_WORKSPACES` 路径）可被 `tool_write_file` / `tool_execute` 正常读写。
2. 多 agent 模式下外部路径写入返回「可解释的拒绝原因」而非静默失败，且不阻塞 120s。
3. 复测「在 D 盘用 React 写俄罗斯方块」：在配置 `EXTRA_WORKSPACES` 后，Agent 能在目标盘完成脚手架 + 代码 + 构建验证，空格/旋转逻辑可运行。
4. 超长任务不再空耗：命中 `MAX_STEPS` 时以结构化的「已完成/未完成/下一步」收尾。
