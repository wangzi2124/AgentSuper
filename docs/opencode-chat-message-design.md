# opencode 聊天信息设计

> 来源：opencode 源码 `E:\project\opencode-dev`（monorepo）
> 本文梳理 opencode 的聊天消息（Chat Message）在服务端的**数据模型**、**存储结构**、**生命周期**与**事件流**，供阅读/仿制参考。
> 关键代码位置：
> - `packages/opencode/src/session/schema.ts` —— ID 定义
> - `packages/opencode/src/session/message.ts` —— Message/Part 的 Effect Schema（V2 视图）
> - `packages/schema/src/v1/session.ts` —— `Info`/`Part`/`Assistant`/`User` 完整类型（存储层使用的数据）
> - `packages/opencode/src/session/message-v2.ts` —— 读写、翻页、LLM 消息转换
> - `packages/opencode/src/session/processor.ts` —— 流式事件消费、Part 更新
> - `packages/opencode/src/session/prompt.ts` —— 一次聊天回合的编排（run loop）
> - `packages/core/src/session/sql.ts` —— SQLite 表结构
> - `packages/schema/src/session-message.ts` / `packages/core/src/session.ts` —— 新一代事件溯源设计（`session_message` 表）

---

## 1. 总体设计思想

opencode 的聊天信息采用 **Session（会话）→ Message（消息）→ Part（部件）** 三层结构：

- **Session** 是一次对话的容器，记录目录、标题、agent、model、token/费用汇总等。
- **Message** 只有两种角色 `user` / `assistant`，是「一条消息」的**骨架**（谁发的、何时发的、用了什么模型、token/费用、错误、关联的父消息）。**消息本身不存正文**。
- **Part** 挂在 Message 下，是真正的**内容载体**：文本、推理、工具调用/结果、文件、step 边界、快照、补丁、compaction 标记、子任务等，全部以带 `type` 判别式的 union 表达。

一句话：**一条消息是一组异构 Part 的有序集合**。这样设计使得：
- 单条助手消息可以同时包含「逐步文本 + 推理 + 多个工具调用 + 快照」，且每部分有独立 ID，可单独流式更新、增量推送、回滚定位。
- 前端可以只监听 Part 级别的 delta 事件做局部刷新，而不必整条重发消息。

```text
Session
 ├─ id, projectID, directory, title, agent, model, cost, tokens, ...
 └─ Message[]            (role: user | assistant)
     └─ info (骨架) + parts[] (正文，异构 union)
         ├─ text / reasoning / file
         ├─ tool / step-start / step-finish
         ├─ snapshot / patch / agent
         ├─ compaction / subtask / retry
```

---

## 2. ID 设计（唯一且可排序）

`packages/opencode/src/session/schema.ts` + `packages/core/src/id/id.ts` + `packages/schema/src/identifier.ts`

| 对象 | 前缀 | 生成方向 | 说明 |
|------|------|---------|------|
| Session | `ses_` | **descending** | 会话 ID，倒序生成 |
| Message | `msg_` | **ascending** | 消息 ID，单调递增 |
| Part | `prt_` | **ascending** | 部件 ID，单调递增 |

- ID = 前缀 + 12 位十六进制时间戳 + 14 位随机字符，共 26 字符。
- 时间戳编码：`timestamp * 0x1000 + counter`（counter 在同毫秒内自增），`ascending` 取原值、`descending` 取按位取反。
- 因此 **MessageID/PartID 单调递增 → 天然可按 ID 排序得到时序**；SessionID 倒序，最新的会话排在最前。
- `MessageID.ascending()` 直接生成新 ID；`MessageID.make(...)` 用于把已存在的字符串包装成品牌类型。

```ts
// packages/schema/src/identifier.ts
export function create(descending: boolean, timestamp = Date.now()) {
  const current = BigInt(timestamp) * 0x1000n + BigInt(counter)
  const value = descending ? ~current : current
  // 6字节时间戳 + 14字节随机
  const time = ...  // 12位hex
  return time + randomChars
}
```

---

## 3. 核心数据结构

### 3.1 Message（Info）

`packages/schema/src/v1/session.ts` 中 `Info = Union(User, Assistant)`，判别式 `role`。

**User（用户消息）**：骨架 + 可选会话级配置

```ts
User {
  id: MessageID
  sessionID: SessionID
  role: "user"
  time: { created }
  format?: "text" | "json_schema"   // 结构化输出格式（含 json schema + retryCount）
  summary?: { title?, body?, diffs[] }  // 本次消息对应的文件变更摘要
  agent: string                       // 发送这条消息时使用的 agent
  model: { providerID, modelID, variant? }
  system?: string                     // 覆盖系统提示
  tools?: Record<string, boolean>     // 本次启用的工具开关
}
```

**Assistant（助手消息）**：含模型调用结算信息

```ts
Assistant {
  id, sessionID, role: "assistant"
  time: { created, completed? }
  error?: 结构化错误           // AuthError / APIError / AbortedError /
                              // ContextOverflowError / ContentFilterError /
                              // OutputLengthError / StructuredOutputError / UnknownError
  parentID: MessageID          // 关联的用户消息（这条回复回答了哪条 user 消息）
  modelID, providerID
  mode: string                 // agent 的 mode（如 "primary" / "subagent" / "compaction"）
  agent: string
  path: { cwd, root }          // 消息产生时的目录上下文
  summary?: boolean            // 是否为 compaction 摘要消息
  cost: Finite
  tokens: { total?, input, output, reasoning, cache: { read, write } }
  structured?: any             // 结构化输出结果
  variant?: string
  finish?: string              // 完成原因（见下）
}
```

**`finish`（完成原因）** 是判断一轮是否结束的关键字段，取自 provider 的 `finish_reason`，常见取值：`stop` / `length` / `tool-calls` / `content-filter` / `error` 等。循环逻辑只对 `tool-calls` 继续（把工具结果回传给模型），其余值意味着该轮结束。

**错误结构**（`Assistant.error`）统一为 `{ name, data }` 形式（TaggedError），如：

```ts
{ name: "APIError", data: { message, statusCode?, isRetryable, responseHeaders?, responseBody?, metadata? } }
{ name: "ContextOverflowError", data: { message, responseBody? } }
{ name: "ProviderAuthError", data: { providerID, message } }
```

### 3.2 Part（消息部件）

`Part = Union([Text, Subtask, Reasoning, File, Tool, StepStart, StepFinish, Snapshot, Patch, Agent, Retry, Compaction])`，判别式 `type`。每个 Part 都有 `id` / `sessionID` / `messageID` 三件套。

| type | 作用 | 关键字段 |
|------|------|---------|
| `text` | 文本内容 | `text`, `synthetic?`(内部注入文本), `ignored?`, `time{start,end}`, `metadata` |
| `reasoning` | 推理过程 | `text`, `metadata`(含 anthropic signature), `time{start,end}` |
| `file` | 文件/图片/媒体 | `mime`, `filename?`, `url`(file: 或 data:), `source?`(file/symbol/resource) |
| `tool` | 工具调用 | `callID`, `tool`, `state`(见下), `metadata` |
| `step-start` | 一步(step)开始 | `snapshot?` |
| `step-finish` | 一步结束 | `reason`, `snapshot?`, `cost`, `tokens` |
| `snapshot` | 代码快照 | `snapshot`(hash) |
| `patch` | 文件补丁 | `hash`, `files[]` |
| `agent` | 唤起子 agent | `name`, `source?` |
| `subtask` | 子任务 | `prompt`, `description`, `agent`, `model?`, `command?` |
| `retry` | 重试记录 | `attempt`, `error`, `time.created` |
| `compaction` | 压缩标记 | `auto`, `overflow?`, `tail_start_id?` |

**Tool Part 的 `state`** 是一个四态有限状态机，是工具调用的核心：

```ts
ToolState = Union([
  { status: "pending",   input: {}, raw: string },              // 参数还在流式生成
  { status: "running",   input, title?, metadata?, time{start} }, // 开始执行
  { status: "completed", input, output, title, metadata,
                          time{start,end,compacted?}, attachments?: FilePart[] },
  { status: "error",     input, error, metadata?, time{start,end} },
])
```

- `pending → running → completed/error`，中间通过 `updatePart` 整条更新，流式阶段用 `updatePartDelta`（只推字段增量，如文本累积）。
- 中断的 tool 会被标记为 `status:"error"` + `metadata.interrupted: true`，回放时视为孤儿，不再触发 assistant 预填充请求（`prompt.ts:isOrphanedInterruptedTool`）。
- 压缩（prune）后会把旧 tool 结果清掉，并打上 `time.compacted` 标记，回放时输出替换为 `[Old tool result content cleared]`。

### 3.3 WithParts（读取返回的聚合）

```ts
WithParts = { info: Info, parts: Part[] }
```

读取时一条 Message 与其 Parts 一次性聚合返回，`message-v2.ts:hydrate` 用一条 `IN` 查询把 parts 按 message 归组。

---

## 4. 存储结构（SQLite）

`packages/core/src/session/sql.ts`。采用 **JSON 列存主体 + 索引列做排序/过滤** 的方式。

### 4.1 `session` 表

```sql
CREATE TABLE session (
  id               TEXT PRIMARY KEY,            -- ses_
  project_id       TEXT NOT NULL,               -- FK project.id (onDelete cascade)
  workspace_id     TEXT,
  parent_id        TEXT,                        -- 子会话（fork/子任务）
  slug             TEXT NOT NULL,
  directory        TEXT NOT NULL,               -- 工作目录
  path             TEXT,                        -- 相对 worktree 的路径
  title            TEXT NOT NULL,
  version          TEXT NOT NULL,
  share_url        TEXT,
  summary_additions/deletions/files  INTEGER,   -- 汇总统计
  summary_diffs    JSON,
  metadata         JSON,
  cost             REAL NOT NULL DEFAULT 0,
  tokens_input/output/reasoning/cache_read/cache_write INTEGER NOT NULL DEFAULT 0,
  revert           JSON,                        -- 回滚状态
  permission       JSON,                        -- 权限规则
  agent            TEXT,
  model            JSON,                        -- {id, providerID, variant}
  time_created/time_updated INTEGER NOT NULL,
  time_compacting  INTEGER,
  time_archived    INTEGER,
);
-- 索引: session_project_idx, session_workspace_idx, session_parent_idx
```

### 4.2 `message` 表 + `part` 表

```sql
CREATE TABLE message (
  id         TEXT PRIMARY KEY,       -- msg_
  session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  time_created, time_updated INTEGER,
  data       JSON NOT NULL,          -- V1MessageData = Info 去掉 id/sessionID
);
-- 索引: message_session_time_created_id_idx(session_id, time_created, id)

CREATE TABLE part (
  id         TEXT PRIMARY KEY,       -- prt_
  message_id TEXT NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,
  time_created, time_updated INTEGER,
  data       JSON NOT NULL,          -- V1PartData = Part 去掉 id/sessionID/messageID
);
-- 索引: part_message_id_id_idx, part_session_idx
```

要点：
- Message/Part 主体以 JSON 存在 `data` 列（模式无关、演进友好）；排序靠 `time_created + id`。
- 级联删除：删 session → 删 message → 删 part。
- **分页查询**（`message-v2.ts:page`）：按 `time_created` 降序 + `id` 降序，`limit+1` 判断是否还有下一页，返回 base64url 编码的游标 `{id, time}`。

### 4.3 新一代 `session_message` 表（事件溯源，V2）

`packages/schema/src/session-message.ts` + `packages/core/src/session/sql.ts` 定义了一个**追加式事件日志**：

```sql
CREATE TABLE session_message (
  id         TEXT PRIMARY KEY,       -- msg_
  session_id TEXT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  type       TEXT NOT NULL,          -- 消息类型（判别式）
  seq        INTEGER NOT NULL,       -- 会话内单调递增序号
  time_created, time_updated INTEGER,
  data       JSON NOT NULL,          -- 各类型的具体数据
);
-- UNIQUE(session_id, seq)
```

消息类型（`SessionMessage.Message` 的 union）：`agent-switched` / `model-switched` / `user` / `synthetic` / `system` / `shell` / `assistant` / `compaction`。

```ts
Assistant {  // 结构扁平，content 直接内嵌
  id, sessionID, type: "assistant",
  agent, model,
  content: [ {type:"text"}, {type:"reasoning"}, {type:"tool", state: pending|running|completed|error} ],
  snapshot?, finish?, cost?, tokens?, error?, time{created,completed?}
}
```

配套表：
- `session_input`（待消费的 prompt 队列，`delivery: steer|queue`，`admitted_seq`/`promoted_seq` 保证顺序）
- `session_context_epoch`（压缩基线，`baseline_seq` + `snapshot`）
- `todo`（任务清单）

这是与旧 `message/part` 表并行的**演进方向**：`session.ts`（V2 Session）直接读 `session_message` 表；而 `packages/opencode/src/session/` 当前主力仍用 `message/part` 表（V1 模型）。两套都基于同一 `session` 表。

---

## 5. 一次聊天回合的生命周期

核心在 `prompt.ts:runLoop`（外层循环）与 `processor.ts:process`（内层流式消费）。

### 5.1 用户发消息（prompt）

1. `SessionPrompt.prompt` → `createUserMessage`：
   - 解析 agent / model / variant（未指定则取会话当前值或 provider 默认）。
   - 创建 `User` info（`MessageID.ascending()`），`sessions.updateMessage(userInfo)` 落库。
   - 解析输入 parts（text / file / agent / MCP resource），每个 part 分配 `PartID.ascending()` 并 `updatePart` 落库。
   - 附件文本化：文本文件会先调 `Read` 工具读取，把内容作为 `synthetic: true` 的 text part 注入；图片归一化（resize）后存 file part。
2. `sessions.touch` 更新会话 `time_updated`；若启用了工具权限变化则 `setPermission`。
3. 进入 `runLoop`。

### 5.2 run loop（多步循环）

`runLoop` 每次迭代：
1. 读取全部消息 `MessageV2.filterCompactedEffect`（按压缩基线过滤/重排，见 §7）。
2. `MessageV2.latest(msgs)` 定位 `lastUser` / `lastAssistant` / `lastFinished` / `tasks`（未处理的 compaction/subtask part）。
3. 判定是否该结束：`lastAssistant.finish` 非 `tool-calls` 且无未完成 tool part → break。
4. 若 `tasks` 里有 `subtask` → `handleSubtask` 直接执行子 agent（生成一个 assistant 消息 + 一个 `tool` part，把子 agent 结果作为 tool 输出）；是 `compaction` → 执行压缩。
5. 需要压缩（`compaction.isOverflow`）→ `compaction.create` 插入 compaction part。
6. **创建 assistant 消息骨架**：`mode/agent = agent.name`、`parentID = lastUser.id`、`cost=0`、`tokens=全 0`、`time.created=now`，`updateMessage` 落库。
7. `processor.create` 绑定此 assistant 消息，`handle.process({ system, messages, tools, model })`。

### 5.3 流式处理（processor）

`processor.process` 通过 `llm.stream` 拉取 `LLMEvent` 流，`Stream.tap(handleEvent)` 逐个事件更新 Part：

| LLM 事件 | 产生的 Part / 更新 |
|---------|-------------------|
| `reasoning-start/delta/end` | 创建/累积/结算 `reasoning` part（delta 用 `updatePartDelta`） |
| `tool-input-start/delta/end` | `ensureToolCall` 创建 `tool` part（`pending`，`raw` 累积参数） |
| `tool-call` | 状态 → `running`，写 `input`；**doom-loop 检测**（同 tool+同 args 连续 3 次 → 权限询问） |
| `tool-result` | 状态 → `completed`，写 `output`/`title`/`metadata`/`attachments` |
| `tool-error` | 状态 → `error` |
| `text-start/delta/end` | 创建/累积/结算 `text` part |
| `step-start` | 写 `step-start` part + 记录快照 `snapshot.track()` |
| `step-finish` | 结算 `usage`→`cost`/`tokens`，写 `step-finish` part（含 tokens），diff 快照 → `patch` part，触发异步总结 `summary.summarize`，并检查是否溢出（溢出 → `needsCompaction`） |
| `finish` | 流结束 |

- 每一步结算后 `assistantMessage.finish = value.reason`，`cost += usage.cost`，`tokens = usage.tokens`，`updateMessage` 落库。
- 工具执行状态保存在 `ctx.toolcalls`（callID → partID），`completeToolCall`/`failToolCall` 用 `updatePart` 写回。
- 中断/清理（`cleanup`）：补全未结算的 text/reasoning part、把未完成 tool 标为 `error + interrupted`、`time.completed` 并最终 `updateMessage`。
- 错误统一走 `halt` → `MessageV2.fromError` 转成结构化 `Assistant.error`（区分 ContextOverflow/API/Auth/Aborted 等），并发布 `session.error` 事件。

### 5.4 回合结束

- `result` 三态：`continue`（正常进入下一轮）、`stop`（阻塞/错误/已完成）、`compact`（需要压缩）。
- `runLoop` 判断 `finish` 且非 `tool-calls` 且无残留 tool part → break。
- `content-filter` finish 会被转成 `ContentFilterError` 并发布 error 事件；`json_schema` 模式未产出结构 → `StructuredOutputError`。
- 最后 `compaction.prune`（清理旧 tool 输出）并返回最后一条 assistant 消息。

---

## 6. 事件流 / 增量推送

`packages/schema/src/v1/session.ts` 底部 + `packages/schema/src/session-event.ts`（V2）。

### 6.1 V1 事件（当前 CLI 主力）

```ts
Event = {
  session.created / session.updated / session.deleted
  message.updated      { sessionID, info }        // 消息骨架变化
  message.removed      { sessionID, messageID }
  message.part.updated { sessionID, part, time }  // 整条 part 更新
  message.part.removed { sessionID, messageID, partID }
  message.part.delta   { sessionID, messageID, partID, field, delta }  // 流式增量
  session.diff         { sessionID, diff }
  session.error        { sessionID?, error }
}
```

- **流式打字效果靠 `message.part.delta`**：只推 `{partID, field:"text", delta:"增量片段"}`，前端把 delta 追加到本地 part 上。
- 每个事件带 `sessionID`（聚合键），客户端按会话过滤订阅。
- 服务端通过 `EventV2Bridge` 统一发布到 SSE（`server.ts` / `packages/schema/src/server-event.ts`），事件被序列化为 `{ type, properties }` 的 `ServerEvent`。

### 6.2 V2 事件（session_message 对应的 durable events）

`packages/schema/src/session-event.ts` 定义了更细粒度的**可持久化事件**（Durable），命名 `session.next.*`：

- `prompted` / `prompt.admitted` / `synthetic`
- `step.started` / `step.ended` / `step.failed`
- `text.started` / `text.delta`(live) / `text.ended`
- `reasoning.started/delta/ended`
- `tool.input.started/delta/ended`、`tool.called`、`tool.progress`、`tool.success`、`tool.failed`
- `compaction.started/delta/ended`
- `agent.switched` / `model.switched` / `moved` / `retried` / `revert.staged/cleared/committed`
- `shell.started/ended`

其中只有「完整值边界」事件是 durable（可回放），`*delta` 事件标记为 live-only（仅实时推送，不持久化）。`session/projector.ts` 负责把这些 durable 事件投影成 `session_message` 表中的记录。

---

## 7. 上下文压缩（Compaction）

`packages/opencode/src/session/compaction.ts` + `message-v2.ts:filterCompacted`。

- **触发**：`step-finish` 结算 token 后 `compaction.isOverflow(...)` 为真（超过模型 context 上限），或收到 ContextOverflowError。
- **执行**：`compaction.create` 插入一条「用户消息 + `compaction` part」，作为占位；`runLoop` 发现 `tasks` 里的 compaction 后调用 `compaction.process`：
  1. 挑选**保留尾部**（`select`：保留最近 N 轮（默认 2 轮）且预算 `preserve_recent_tokens` 内，`tail_start_id` 指向保留起点）。
  2. 把头部历史（去掉已压缩的轮次）用 `MessageV2.toModelMessagesEffect` 转成 LLM 消息，附上压缩 prompt（`<conversation-checkpoint>` 风格，含之前 summary），生成一条 `summary: true` 的 assistant 摘要消息。
  3. 摘要写好后更新 `compaction part.tail_start_id`，后续读取时 `filterCompacted` 会把「摘要消息 + 保留尾部 + 后续消息」重组顺序喂给模型。
- **读取端重排**（`message-v2.ts:filterCompacted`）：把历史整理成 `[compaction用户消息, summary assistant, ...tail保留尾部..., 后续消息]`，且会折叠已完成的压缩轮次。
- **prune**：对旧 tool 输出做「清空但保留占位」——超过保护阈值后把更早 tool part 的输出替换为空并标记 `time.compacted`，避免上下文膨胀。
- 会话级 `session_context_epoch` 记录压缩基线 `baseline_seq`，`history.load` 按 `max(epoch.baseline_seq, 最新compaction seq)` 过滤。

---

## 8. 消息 → LLM prompt 的转换

`message-v2.ts:toModelMessagesEffect`（V1，当前主力）：

- `user` 消息：text part 逐条映射；file part 转成 `{type:"file", url, mediaType, filename}`；`text/plain`/目录附件被忽略（已文本化）；compaction part → 用户消息 `"What did we do so far?"`；subtask → `"The following tool was executed by the user"`。
- `assistant` 消息：text / reasoning / step-start 原样映射；`tool` part 转成 `tool-<name>` 的调用/结果（`state: output-available` 或 `output-error`）；已完成输出被截断（`toolOutputMaxChars`，默认 2k）；`reasoning` 只在模型相同时作为 reasoning 保留，换模型则降级为普通文本。
- **媒体注入**：对不支持在 tool 结果里带图片/PDF 的 provider（如 Bedrock/xAI），把媒体提取成一条合成的 user 消息（`SYNTHETIC_ATTACHMENT_PROMPT = "Attached media from tool result:"`）。
- 最后用 AI SDK 的 `convertToModelMessages` 产出最终模型消息数组。

`core/src/session/runner/to-llm-message.ts`（V2，session_message → `@opencode-ai/llm` Message）：

- `shell` → user 消息 `Shell command: ...\n\n<output>`
- `compaction` → user 消息，包在 `<conversation-checkpoint>` 块内（summary + recent）
- `agent-switched` / `model-switched` → 丢弃
- assistant 里 `provider.executed === true` 的 tool → 直接拼 call+result，避免重复发送。

---

## 9. 前端渲染要点

- 前端通过 SSE 订阅上述事件，按 `sessionID` 过滤；收到 `message.part.updated` 更新整条 part，收到 `message.part.delta` 对本地文本追加增量。
- 每条 assistant 消息可以同时渲染多个 part：`step-start` 分块、`text` 气泡、`reasoning` 折叠区、`tool` 状态卡片（pending→running→completed/error）、`patch`/`snapshot` 文件变更、`file` 图片预览。
- `step-finish` part 携带 tokens，用于展示每步消耗。
- 回滚/删除：`session.revert`（staged/clear/commit）与 `message.removed` / `part.removed` 事件驱动前端恢复历史状态。

---

## 10. 与 AgentSuper 的对照（速览）

AgentSuper 后端 `backend/app/session/` 大量借鉴了同一套模型：

| 概念 | opencode | AgentSuper |
|------|----------|-----------|
| 会话 | `session` 表 + `SessionID(ses_)` | `sessions` 表 |
| 消息骨架 | `message` 表 + `Info(User/Assistant)` | `session_messages` 表 + `Message.type`（user/assistant/system/tool/compaction/epoch） |
| 内容部件 | `part` 表 + `Part` union | `message_parts` 表 |
| 追加事件日志 | `session_message` 表 + `seq` | `session_messages` 的 `seq`（`BEGIN IMMEDIATE` 内 `MAX(seq)+1`） |
| 压缩基线 | `session_context_epoch.baseline_seq` | `session_context_epoch` |
| 输入队列 | `session_input`（steer/queue） | `session_inputs`（delivery: steer/queue） |
| 增量推送 | `message.part.delta` | SSE `tool_delta`/`text_delta` 等（PartUpdated 风格） |

---

## 附：关键文件速查

| 文件 | 内容 |
|------|------|
| `packages/opencode/src/session/schema.ts` | MessageID/PartID/SessionID 品牌类型 |
| `packages/opencode/src/session/message.ts` | 精简版 Message/Part Schema（ToolCall/ToolResult/MessagePart/Info） |
| `packages/schema/src/v1/session.ts` | `User`/`Assistant`/`Part`/`WithParts`/事件定义（完整权威） |
| `packages/schema/src/session-message.ts` | V2 扁平消息（session_message 表） |
| `packages/opencode/src/session/message-v2.ts` | 消息读写、分页、LLM 转换、错误归一化 |
| `packages/opencode/src/session/session.ts` | Session 服务（V1 实现） |
| `packages/core/src/session.ts` | V2 Session（事件溯源） |
| `packages/core/src/session/sql.ts` | 所有表结构 |
| `packages/opencode/src/session/processor.ts` | 流式事件 → Part 更新 |
| `packages/opencode/src/session/prompt.ts` | 回合编排（runLoop / handleSubtask / shell） |
| `packages/opencode/src/session/compaction.ts` | 上下文压缩 / prune |
| `packages/core/src/session/projector.ts` | V2 durable 事件 → session_message 投影 |
