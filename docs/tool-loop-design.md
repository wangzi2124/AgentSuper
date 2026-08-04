# 工具循环调用设计（对齐 opencode 源码）

> 参考 opencode 的 **Agent 工具循环调用** 设计，梳理「LLM 调用 → 工具执行 → 循环/收尾」的
> 完整语义，适配本项目的 **FastAPI + LangGraph + LiteLLM** 技术栈。
>
> opencode 核心源码（本次精读）：
> - 主循环：`packages/opencode/src/session/prompt.ts`（`runLoop`：finish 驱动结束、task 队列、overflow 压缩、MAX_STEPS 注入）
> - 单步处理器：`packages/opencode/src/session/processor.ts`（LLM 事件流 → part 持久化 → tool-call 生命周期 → doom-loop 检测）
> - 工具解析与执行：`packages/opencode/src/session/tools.ts`（registry → AI SDK `tool()` 包装 → `ctx.ask` 权限 / 并行执行）
> - 步骤上限：`packages/core/src/session/runner/max-steps.ts`（`MAX_STEPS_PROMPT` 文案）
> - 重试策略：`packages/opencode/src/session/retry.ts`（指数退避、`retry-after` 头、5xx 必重试、溢出不重试）
> - 工具错误：`packages/opencode/src/tool/tool.ts`（`InvalidArgumentsError` → 回传 LLM 重写参数）

---

## 1. 背景与现状

### 1.1 现状工具循环（`backend/app/agent/graph.py` `_generate`）

当前实现为**单函数内的 `while` 循环**（`graph.py:508-682`）：

```
rounds = 0
while msg.tool_calls and rounds < max_tool_rounds:      # 轮数驱动
    rounds += 1
    prune_tool_outputs(...)                              # 回溯清理旧工具输出
    compactor.should_compact() → compact()               # 上下文压缩
    messages.append(assistant 带 tool_calls)
    for tc in msg.tool_calls:
        args = json.loads(...)                           # 解析失败 → 错误结果
        dedup.make_key → 缓存命中直接复用                  # 工具去重
        tool_tasks.append(_execute_tool(...))            # 权限审批 / 执行
    tool_results = await asyncio.gather(*tool_tasks)     # 并行执行
    变更类工具 → dedup.clear()                            # 写操作后清缓存
    messages.append(tool 结果，bound_tool_output 截断)
    doom-loop 指纹检测 → 注入 DOOM_LOOP_PROMPT            # 仅提示
    rounds >= effective_max_steps → 注入 MAX_STEPS_PROMPT + 禁用工具
    truncate_messages + sanitize_tool_messages
    final_tool_defs = None if injected else tool_defs
    response = llm_call(...)
```

### 1.2 与 opencode 的关键差距

| # | 差距 | opencode | 本项目现状 |
| --- | --- | --- | --- |
| 1 | 步骤上限语义 | 单一 `agent.steps`（`prompt.ts:1178`），`isLastStep = step >= maxSteps` | `max_tool_rounds`(24) 与 `max_steps`(40) 双变量 + `min()` 取生效值，语义重叠易混淆 |
| 2 | 结束语义 | `finish` 驱动：`finish` 非 `tool-calls` 且无未完成工具调用 → break（`prompt.ts:1106-1130`） | 以 `msg.tool_calls` 为空为出口，基本等价但未显式建模 `finish` |
| 3 | 收尾提示角色 | `MAX_STEPS_PROMPT` 以 **assistant 消息**注入（`prompt.ts:1281`），模型自行遵守 | 以 **user 消息**注入，API 层再禁用工具 |
| 4 | doom-loop 检测 | `DOOM_LOOP_THRESHOLD=3`，最近 3 个 tool part 同工具同输入 → `permission.ask(doom_loop)`（人工裁决） | 跨轮指纹连续相同 → 注入提示，**无升级/强停**，可无限提示 |
| 5 | 工具执行 | registry → AI SDK `tool()`，AI SDK 内部并行执行 + abort signal（`tools.ts:99-133`） | `asyncio.gather` 并行，无 abort 通道 |
| 6 | 权限拒绝语义 | `experimental.continue_loop_on_deny=false` 时权限拒绝 → `shouldBreak` → 进程 stop（`processor.ts:633,680`） | 权限拒绝返回可解释错误给 LLM 继续尝试（有意适配：无人工环境更鲁棒） |
| 7 | 重试策略 | `retry.ts`：5xx 必重试、`retry-after` 头、溢出不重试 | litellm `num_retries=2` + TaskRunner 退避，未显式排除溢出 |

---

## 2. opencode 参考设计（源码精读）

### 2.1 runLoop 主循环（`prompt.ts:1081-1341`）

```
while True:
    msgs = filterCompacted(sessionID)                    # 只取未压缩历史
    lastUser / lastAssistant / tasks = latest(msgs)
    hasToolCalls = lastAssistant 含 tool part 且 !providerExecuted 且非孤立中断调用
    if lastAssistant.finish 且 非 "tool-calls" 且 !hasToolCalls 且 lastUser.id < lastAssistant.id:
        break                                             # ← 结束语义：finish 驱动
    step++
    task = tasks.pop()
    if task == "subtask":   handleSubtask(...); continue
    if task == "compaction": compaction.process(...); break if stop; continue
    if lastFinished 且溢出:  compaction.create(auto); continue   # overflow → 压缩续跑
    maxSteps = agent.steps ?? Infinity                    # 单一步骤上限
    isLastStep = step >= maxSteps
    msg = new assistant message;  sessions.updateMessage(msg)
    handle = processor.create({ assistantMessage, sessionID, model })
    result = handle.process({
        messages: [...modelMsgs,
                   (isLastStep ? [{ role: "assistant", content: MAX_STEPS_PROMPT }] : [])],
        tools, toolChoice: json_schema ? "required" : undefined,
    })
    if result == "stop":    break                         # blocked / error
    if result == "compact": compaction.create(auto, overflow); continue
    # result == "continue" → 回到 while 顶部，重新判定 finish
```

关键点：

- **结束语义是 `finish` 驱动**（`prompt.ts:1106-1130`）：`stop_reason` 为 `tool-calls` 不算结束；只要还有未执行的工具调用，即使 finish 为 `stop` 也继续循环。
- **overflow 检测在循环层**（`prompt.ts:1161-1168`）：上次生成已溢出 → 先压缩再续跑，由合成 user 消息驱动。
- **MAX_STEPS_PROMPT 注入在 process 调用内**（`prompt.ts:1281`）：作为 `role: "assistant"` 的追加消息与当前轮一起发给模型；`isLastStep` 时由模型遵守"不再调用工具"。opencode **不**在 API 层移除 tools，靠提示词约束。
- 步骤上限与轮数统一为 **`agent.steps` 一个变量**；`MAX_TOOL_ROUNDS` 之类不再单独存在。
- 压缩 / 子任务以 **task 队列**插入循环，与主线程串行复用同一处理器。

### 2.2 单步处理器（`processor.ts:98-683`）

- 每个 assistant 消息一个 `Handle`（`updateToolCall` / `completeToolCall` / `process`）。
- `process()` 消费 LLM 流式事件（`processor.ts:627-683`）：
  - `tool-input-*` / `tool-call` → `ensureToolCall` 建立 tool part；
  - `tool-call` → 记录 `tool` + `input`，然后做 **doom-loop 检测**（`processor.ts:331-381`）；
  - `tool-result` → `completeToolCall`（含图片附件归一化/超限裁剪，`processor.ts:383-414`）；
  - `tool-error` → `failToolCall`（错误作为工具结果回传，不崩溃）；
  - `step-finish` → 更新 `finish` reason / usage / cost，并做 overflow 检测 → `needsCompaction`（`processor.ts:435-483`）。
- **doom-loop 检测**（`processor.ts:356-380`）：

```
parts = 当前 assistant 消息最近 DOOM_LOOP_THRESHOLD(3) 个 part
if 恰好 3 个 且 全部是 tool 且 tool 名相同 且 state.input JSON 相同:
    permission.ask({ permission: "doom_loop", patterns: [tool], always: [tool] })
    # 默认 ask：用户批准继续，拒绝 → shouldBreak
```

- process 结果三态（`processor.ts:679-681`）：
  - `"compact"` — `needsCompaction`（上下文溢出）；
  - `"stop"` — `blocked`（权限拒绝）或 assistant 消息带 error；
  - `"continue"` — 其余情况回到 runLoop 重新判定。
- **权限拒绝 → 停止**：`shouldBreak = !config.experimental.continue_loop_on_deny`（`processor.ts:633`）。

### 2.3 工具解析与执行（`tools.ts:41-133`）

- `SessionTools.resolve()` 从 `registry.tools()` 收集当前 agent/model 可用工具，逐个包装成 AI SDK `tool()`（`tools.ts:99-133`）。
- 每个工具执行包在 `Effect` 中：`plugin.trigger("tool.execute.before/after")` → `item.execute(args, ctx)` → 结果归一化（attachments 补 id/sessionID/messageID）。
- **`ctx` 提供运行时能力**（`tools.ts:59-90`）：
  - `abort` — AI SDK 的 `AbortSignal`，支持中断；
  - `metadata(val)` — 流式更新 tool part 的 title/metadata（前端实时显示）；
  - `ask(req)` — 权限询问，合并 agent ruleset + session 权限。
- 并行性由 AI SDK 管理：一次返回多个 tool-calls 时并发执行，`settleToolCall` 等全部结束后续传。

### 2.4 步骤上限（`max-steps.ts`）

`MAX_STEPS_PROMPT` 全文语义：**CRITICAL - MAXIMUM STEPS REACHED** → 工具已禁用 → 必须纯文本回复，内容含：
1. 已完成工作摘要；
2. 未完成清单；
3. 后续建议。

本项目已复刻中文版（`graph.py:15-25`），文案结构一致。

### 2.5 重试策略（`retry.ts`）

- `retryable(error, provider)` 判定（`retry.ts:68-152`）：
  - **`ContextOverflowError` → 永不重试**（转压缩，`retry.ts:70`）；
  - APIError：**5xx 一律重试**，即使 SDK 未标记；`isRetryable` 标记、429、`too_many_requests`、rate-limit 文本模式等也重试；
  - 其余按错误文本/JSON code 匹配 rate-limit / overloaded。
- `delay(attempt, error)`（`retry.ts:35-66`）：优先 `retry-after-ms` / `retry-after` 头（秒或 HTTP date），否则 `2s × 2^(n-1)` 指数退避，无头时封顶 30s，有头封顶 2^31-1 ms。
- `policy()`（`retry.ts:176-199`）：Schedule 实现，每次重试前向 status 写入 `{ attempt, message, action, next }`（前端可展示重试进度）。

---

## 3. 目标设计（适配 AgentSuper）

### 3.1 步骤上限统一（`config.py`）

- 语义澄清：**`MAX_STEPS` 为主步骤上限**（对齐 opencode `agent.steps`，默认 40）；`MAX_TOOL_ROUNDS` 降级为**硬兜底**（默认 24），防止配置极值时失控。
- 生效上限 `effective_max_steps = min(max_tool_rounds, max_steps)`，**注入轮 = 生效上限**，保证「最后一轮注入收尾提示 + 禁用工具」在两种配置关系下都可预期：
  - `max_steps < max_tool_rounds`：在 `max_steps` 轮注入；
  - `max_steps >= max_tool_rounds`：在 `max_tool_rounds` 轮注入（等价于兜底触发，同样收尾）。
- 收尾语义与 opencode 一致：注入后**该轮不再允许工具**（本项目采用 API 层 `tool_defs=None`，比 opencode 的"纯提示词"更强），模型必须产出「已完成/未完成/下一步」式总结。

### 3.2 结束语义

- 保留「`msg.tool_calls` 为空自然退出」为正常出口（与 opencode finish 驱动等价：模型无工具调用即结束本轮）。
- 显式处理两类**非自然退出**：
  1. `effective_max_steps` 触发 → MAX_STEPS_PROMPT + 禁用工具收尾；
  2. doom-loop 升级（见 3.3）→ 强制收尾。
- 移除 `msg.tool_calls` 死循环残留分支中的重复逻辑，统一走「禁用工具收尾」路径（见 3.4）。

### 3.3 doom-loop 分级处理（`graph.py`）

对齐 opencode `permission.ask(doom_loop)` 的「人工裁决」语义，无人工环境下改为**两级升级**：

1. **第一级（提示）**：连续 `DOOM_LOOP_THRESHOLD`(默认 3) 轮指纹相同 → 注入 `DOOM_LOOP_PROMPT`，清空指纹（现状行为）；
2. **第二级（强停）**：升级后再连续触发（`DOOM_LOOP_MAX_STRIKES`，默认 2 次警告后）→ 不再提示，直接注入 `MAX_STEPS_PROMPT` + 禁用工具，强制模型产出收尾总结，结束本轮。

这与 opencode「deny → shouldBreak → stop」的最终效果一致（opencode 拒绝后同样退出循环收尾）。

### 3.4 强制收尾统一

- 循环内 `MAX_STEPS` / doom-loop 升级两个入口都设置 `steps_prompt_injected=True`，统一经「注入收尾提示 + `final_tool_defs=None`」收尾。
- 循环外兜底分支（`graph.py:626-671`，理论不可达的保险路径）：
  - 保留「先执行剩余 tool_calls、再强制总结」的尽力行为；
  - 但最终总结调用同样以 `tool_defs=None` 调用（对齐「达到上限工具禁用」语义），并复用 `MAX_STEPS_PROMPT` 文案（当前文案一致，仅统一文案来源）。
- 收尾提示消息角色对齐 opencode：以 `role: "assistant"` 注入（`prompt.ts:1281`）。

### 3.5 错误分类与重试

- **权限拒绝 → 不重试**：维持 `_permission_denied_msg` 可解释错误（已实现），区别于可重试的临时错误。
- **参数解析失败 → 不重试**：`json.loads` 失败即回传错误让模型重写参数（对齐 `InvalidArgumentsError`，已实现）。
- **上下文溢出 → 压缩而非重试**：`compactor.should_compact` 在每轮开头检查（已实现）；litellm `num_retries=2` 仅兜底网络/5xx（与 `retry.ts` 的 5xx 必重试一致）。
- **工具执行异常 → 错误字符串回传**：`Error executing <tool>: <e>` 让模型自行调整（已实现）。

### 3.6 并行工具调用

- 保留 `asyncio.gather` 并行执行一次返回的多个 tool_calls（与 AI SDK 并行语义一致）。
- `ToolResultDedup` 保留：同一 `tool+args` 轮内复用结果；变更类工具执行后清缓存（已实现）。
- 说明：本项目无 opencode 的 abort signal 通道，中断由外层 session 管理（`interrupt`）负责，不在本轮工具循环内实现。

---

## 4. 落地实施计划

| 阶段 | 改动 | 涉及文件 |
| --- | --- | --- |
| P1 语义澄清 | `MAX_STEPS` / `MAX_TOOL_ROUNDS` 注释更新（主上限 + 硬兜底） | `backend/app/config.py` |
| P2 收尾统一 | 循环内 MAX_STEPS 注入、收尾消息角色对齐 assistant、`final_tool_defs=None` | `backend/app/agent/graph.py` |
| P3 doom-loop 升级 | 新增 `DOOM_LOOP_MAX_STRIKES` 二级强停逻辑 | `backend/app/agent/graph.py`、`backend/app/config.py` |
| P4 兜底分支对齐 | 循环外强制收尾调用改为 `tool_defs=None`，统一文案 | `backend/app/agent/graph.py` |
| P5 文档 | 新设计文档 + AGENTS.md 相关小节更新 | `docs/tool-loop-design.md`、`AGENTS.md` |

**验收标准**：

1. `MAX_STEPS` 与 `MAX_TOOL_ROUNDS` 无论大小关系，命中上限时都以「收尾提示 + 工具禁用」结束，模型输出包含已完成/未完成/下一步。
2. doom-loop 首次注入策略提示；持续重复时第二次警告后强制收尾（不再无限提示）。
3. 正常情况下（模型自然停止工具调用）循环照常自然退出，无行为回归。
4. 后端 `main` 可导入、`_generate` 主路径可用（无工具循环的最小请求可正常回答）。
