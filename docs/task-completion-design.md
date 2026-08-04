# 任务完成判断设计（对齐 opencode）

> 梳理 opencode **如何判定一轮任务是否完成**（何时停止工具循环、何时认为已答完），
> 适配本项目的 **FastAPI + LangGraph + LiteLLM** 技术栈。
>
> opencode 核心源码（本次精读）：
> - 统一结束原因：`packages/llm/src/schema/ids.ts`（`FinishReason` 枚举）
> - 各 Provider 归一化：`packages/llm/src/protocols/openai-chat.ts` / `anthropic-messages.ts` / `gemini.ts` / `bedrock-converse.ts` / `openai-responses.ts`、`packages/opencode/src/session/llm/ai-sdk.ts`
> - 单步处理器写入 finish：`packages/opencode/src/session/processor.ts`（`step-finish` → `assistantMessage.finish`）
> - 最近消息解析：`packages/opencode/src/session/message-v2.ts`（`latest()`：user / assistant / finished / tasks）
> - 主循环出口：`packages/opencode/src/session/prompt.ts`（`runLoop` 顶部 finish 判定 + 底部 `stop/compact/continue`）
> - 核心 runner：`packages/core/src/session/runner/llm.ts`（`needsContinuation` 续跑判定）

---

## 1. 背景与现状

### 1.1 现状工具循环（`backend/app/agent/graph.py` `_generate`）

当前循环出口只有**两个**（`graph.py:519`）：

```
while msg.tool_calls and rounds < max_tool_rounds:
    ...
```

- **自然出口**：`msg.tool_calls` 为空（模型本轮没有请求工具 → 视为回答完成）。
- **护栏出口**：`rounds >= max_tool_rounds`（硬兜底）或 `MAX_STEPS` 注入（收尾总结）。

现状**完全不读取** `response.choices[0].finish_reason`（`grep finish_reason` 无任何结果）。
LiteLLM 返回体自带 OpenAI 式 `finish_reason`（`stop` / `length` / `tool_calls` / `content_filter`），当前被丢弃。

### 1.2 与 opencode 的关键差距

| # | 差距 | opencode | 本项目现状 |
| --- | --- | --- | --- |
| 1 | 完成判据 | `finish`（`FinishReason` 枚举）驱动，`tool-calls` 明确不算完成 | 仅看 `tool_calls` 是否为空，未读 `finish_reason` |
| 2 | `length`（输出截断） | 归一化为 `length`，作为「已结束但可能不完整」显式记录 | 不识别；若 `length` 时无 tool_calls，被当作正常完成，截断无感 |
| 3 | `content-filter`（内容过滤） | 归一化为 `content-filter`，`runLoop` 把它转成错误暴露（`prompt.ts:1301-1308`） | 不识别，静默当作普通停止 |
| 4 | `unknown`（未知原因） | 归一化为 `unknown`，不视为已结束（`prompt.ts:1295` 排除） | 不识别 |
| 5 | providerExecuted 工具 | 工具可由 Provider 端执行（标记 `providerExecuted`），不再回传模型 | 无此概念（全部本地执行） |
| 6 | 孤儿中断工具 | `isOrphanedInterruptedTool`：`error + interrupted` 不算待办工作（`prompt.ts:96-100`） | 无中断/孤儿概念 |

### 1.3 目标

对齐 opencode 的 **`finish` 驱动完成判定**：

1. **读取并归一化 `finish_reason`**：`stop` / `length` / `tool-calls` / `content-filter` / `error` / `unknown`。
2. **循环出口对齐 `finish` 语义**：只有 `stop` / `length` 且无 tool_calls 才算真正完成；`tool-calls` / `unknown` 不算完成。
3. **`length` 显式处理**：结果不完整时给出提示（叠加在答案尾部或日志），不静默当正常。
4. **`content-filter` 转错误**：返回可解释消息，前端可感知被拦截。
5. 保持现有 `MAX_STEPS` / doom-loop / 压缩护栏不变。

---

## 2. opencode 参考设计（源码精读）

### 2.1 统一结束原因（`ids.ts:39`）

```
FinishReason = "stop" | "length" | "tool-calls" | "content-filter" | "error" | "unknown"
```

- `stop`：模型自然结束（本轮无更多输出）。
- `length`：输出达到 token 上限被截断（**已结束但内容不完整**）。
- `tool-calls`：模型请求调用工具（**不算结束**，需把工具结果回传后继续）。
- `content-filter`：内容被 Provider 过滤（视为错误）。
- `error`：生成出错。
- `unknown`：Provider 未给出可识别原因（不视为已结束）。

### 2.2 Provider 归一化（各协议 `mapFinishReason`）

| Provider | 原生值 | 归一化 |
| --- | --- | --- |
| OpenAI Chat（`openai-chat.ts:378-384`） | `stop`→`stop`；`length`→`length`；`content_filter`→`content-filter`；`function_call`/`tool_calls`→`tool-calls`；其余→`unknown` | `FinishReason` |
| Anthropic（`anthropic-messages.ts:558`） | `end_turn`→`stop`；`max_tokens`→`length`；`tool_use`→`tool-calls`；`stop_sequence`/`pause_turn`→`unknown`… | `FinishReason` |
| Gemini（`gemini.ts:363`） | `STOP`→`stop`；`MAX_TOKENS`→`length`；有 tool-call→`tool-calls`… | `FinishReason` |
| Bedrock（`bedrock-converse.ts:431`） | 类似映射 | `FinishReason` |
| AI SDK 桥（`ai-sdk.ts:21-23`） | `Schema.is(FinishReason) ? value : "unknown"` | `FinishReason` |

**关键：所有 Provider 的停止原因统一成 6 值枚举，业务逻辑只跟枚举打交道。**

### 2.3 单步处理器写入 finish（`processor.ts:435-443`）

```
case "step-finish":
    ...
    ctx.assistantMessage.finish = value.reason   # ← LLM 事件流的 finish 写入 assistant 消息
    ...
```

- 每条 assistant 消息带一个 `finish` 字段。
- `process()` 返回三态（`processor.ts:679-681`）：`"compact"`（上下文溢出）/ `"stop"`（blocked 或 error）/ `"continue"`。

### 2.4 最近消息解析（`message-v2.ts:latest`，`message-v2.ts:585-601`）

```
user     = 最新 user 消息
assistant = 最新 assistant 消息（无论是否结束）
finished = 最新带 finish 字段的 assistant 消息   ← 关键：只有 finish 才算"已结束"
tasks    = 比最新 finished 更新的 user 消息上挂的 compaction/subtask 任务
```

- **「已结束」= 消息上有 `finish` 字段**；没有 finish 的 assistant 视为仍在处理中。
- `runLoop` 每轮从这里拿到 `finished`，判断是否需要继续。

### 2.5 runLoop 顶部：循环继续 / 退出判定（`prompt.ts:1106-1130`）

```
hasToolCalls =
  lastAssistant 的 parts 中存在 type=="tool" 且 !providerExecuted 且 !isOrphanedInterruptedTool(part)

if (
  lastAssistant?.finish &&
  !["tool-calls"].includes(lastAssistant.finish) &&   // finish 不是 tool-calls
  !hasToolCalls &&                                    // 没有待回传的本地工具
  lastUser.id < lastAssistant.id                      // 用户消息早于该 assistant（正常轮次）
) {
  break   // ← 真正完成
}
step++
... 否则继续下一轮（处理 tasks / compaction / overflow / 再调一次 LLM）
```

**完成判定 = 三条同时满足**：
1. 最新 assistant **有 finish**；
2. finish **不是 `tool-calls`**（tool-calls 表示还有工具要跑，绝不结束）；
3. 没有 **待执行的本地工具**（`providerExecuted` 的工具不算，孤儿中断工具不算）。

注意：循环出口**只排除 `tool-calls`**——`stop` / `length` / `content-filter` / `error` / `unknown`
都会退出循环；`unknown` 的差异只体现在下面的 error-surfacing 判定，不影响循环出口。

### 2.6 runLoop 底部：`stop / compact / continue` 分支（`prompt.ts:1295-1329`）

```
const finished = handle.message.finish && !["tool-calls", "unknown"].includes(handle.message.finish)
# ↑ 仅用于"是否触发错误暴露 / 结构化输出校验"的判定：
#   content-filter → 转 ContentFilterError（prompt.ts:1301-1308）
#   json_schema 未产出 → 转 StructuredOutputError

if (result === "stop")    break                       # blocked / error
if (result === "compact") compaction.create(...)      # 溢出→压缩→下一轮 continue
return "continue"                                     # 回到循环顶部重新判定
```

- **`content-filter` 不是静默结束**：转成错误暴露给前端（`prompt.ts:1301-1308`）。
- **`unknown` 不进 error-surfacing**（`prompt.ts:1295` 排除），但循环仍会退出。
- `overflow` 走压缩续跑，不当作完成（`prompt.ts:1161-1168`）。

### 2.7 核心 runner 续跑判定（`core/.../runner/llm.ts`）

```
tool-call 事件（且非 providerExecuted）→ needsContinuation = true        # 有工具待回传
stream 结束后：
  needsContinuation = !providerError && needsContinuation
while (needsContinuation) { runTurn(); needsContinuation = ... }          # 续跑循环
```

- 与 `runLoop` 语义一致：**有 tool-call 即续跑**，直到无工具调用且 Provider 正常结束。

### 2.8 孤儿中断工具（`prompt.ts:96-100`）

```
isOrphanedInterruptedTool(part) =
  part.state.status === "error" && part.state.metadata?.interrupted === true
```

- 被中断/清理的工具 part 标记为 `error + interrupted`，**不算待办工作**，不阻碍循环退出（否则中断后永远无法结束）。

---

## 3. 目标设计（适配 AgentSuper）

### 3.1 归一化 finish_reason（新增 helper）

```
def _normalize_finish_reason(finish_reason: str | None) -> str:
    # OpenAI 式 finish_reason → opencode FinishReason 六值
    "stop"          → "stop"
    "length"        → "length"
    "tool_calls"    → "tool-calls"   (兼容 "function_call")
    "content_filter"→ "content-filter"
    None/其他       → "stop"          (无信息时视为正常结束；None 是本地无异常缺省)
```

- 放在 `graph.py` 模块级，单一映射表，便于扩展其他 Provider 的原生值。

### 3.2 循环出口对齐 finish 语义（`_generate`）

改 `while msg.tool_calls ...` 为「**读取 finish + 结合 tool_calls**」，对齐 opencode
`prompt.ts:1113` 的「只排除 tool-calls」语义：

```
while (
    (msg.tool_calls or finish_reason == "tool-calls")   # 有工具待执行 / 请求了工具但本地未执行
    and rounds < max_tool_rounds
):
    ... 每轮末尾重新读 finish_reason ...
```

- 循环出口：`finish_reason != "tool-calls"` 且 `msg.tool_calls` 为空（与 opencode 一致，
  `stop` / `length` / `content-filter` / `error` / `unknown` 都会退出循环）。
- `MAX_STEPS` / doom-loop / compaction 逻辑不变。

### 3.3 `length`（截断）显式处理

- 退出循环后若 `finish_reason == "length"`：答案可能不完整。
- 处理：**在答案尾部追加提示**「⚠️ 输出因达到 token 上限被截断，可能不完整」，并把
  `finish_reason` 记入返回的 `steps` 事件（前端可展示）。
- 不重试（与 opencode 一致：`length` 是"已结束但不完整"，不自动续跑）。

### 3.4 `content-filter`（过滤）转错误

- 退出循环后若 `finish_reason == "content-filter"`：把 `answer` 置为可解释错误文案
  「模型回答被内容安全策略拦截，未返回完整内容」，并 push `step_end` 事件标记 `status="error"`。
- 对齐 opencode `prompt.ts:1301-1308` 的"过滤 → 错误暴露"。

### 3.5 其他 finish 值

- `stop`：正常完成（现状行为不变）。
- `unknown`：不识别原因，但**同样退出循环**（对齐 opencode 循环出口只排除 tool-calls）；
  按正常完成收尾，仅在日志记录。
- `error`：litellm 抛异常由调用方处理；若响应内自带 error finish，按错误收尾。

### 3.6 保留现有护栏

- `MAX_STEPS` + `MAX_STEPS_PROMPT` 收尾、doom-loop 分级、压缩/截断、`ToolResultDedup` 均不变。

---

## 4. 落地实施计划

| 阶段 | 改动 | 涉及文件 |
| --- | --- | --- |
| P1 归一化 | 新增 `_normalize_finish_reason` 映射 helper | `backend/app/agent/graph.py` |
| P2 循环出口 | `_generate` 读取 `finish_reason`，`while` 条件并入 finish 语义 | `backend/app/agent/graph.py` |
| P3 收尾处理 | `length` 尾部提示 / `content-filter` 错误化，写 `steps` 事件 | `backend/app/agent/graph.py` |
| P4 文档 | 设计文档 + AGENTS.md 相关小节 | `docs/task-completion-design.md`、`AGENTS.md` |

**验收标准**：

1. 正常对话（模型 finish=stop）行为不变，无回归。
2. 模拟 `finish_reason="length"` 的响应：答案尾部出现截断提示，不静默。
3. 模拟 `finish_reason="content_filter"`：返回内容为可解释错误文案，前端可见 error 标记。
4. 工具循环仍以"模型请求工具"驱动续跑，`MAX_STEPS` / doom-loop 护栏不受影响。
5. 后端 `main` 可导入、`py_compile` 通过。
