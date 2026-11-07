# AgentSuper Web Chat v2 全量重构设计（对齐 OpenCode）

> 状态：设计稿，不含实现。
> 依据：`C:\Users\MAC\Desktop\opencode-docs-full.md`、`C:\Users\MAC\Desktop\opencode-openapi.json`、当前 AgentSuper 代码。
> 结论：不再在现有 turn-centric SSE 上做补丁，按 OpenCode 的 durable session / inbox / message-part / global event 模型整体重构。
>
> 命名约定：文中 OpenCode 示例保留其 `camelCase`；AgentSuper v2 契约统一使用 `snake_case`（见 `backend/app/contracts/v2.py` 与 `frontend/src/contracts/v2.ts`）。

---

## 1. 决策摘要

1. **消息发送改为“持久投递”**：`POST prompt` 只做 admission，立即返回 inbox item；执行在后台，结果通过全局事件流推送。
2. **取消每会话/每回合 SSE**：全前端只保留一条 `/api/v2/event` 连接，所有 tab、所有会话共享。
3. **消息真相源改为 `message + part`**：文本、推理、工具调用都是稳定 part；live 事件和 REST 重放消费同一套 part，不再有 `agent_step` 派生模型。
4. **会话状态由后端维护**：`idle / busy / retry`、inbox、权限、压缩、revert、fork、usage 都有明确事件，不靠前端猜。
5. **前端 tab 只保存视图身份**：tab 记录 session id/path/title；会话运行时状态在独立 store，关闭 tab 不等于中断执行。
6. **一次性切换，不长期双轨**：新增 `/api/v2`，旧 `/api/chat/multi-agent/stream` 冻结；功能对齐后删除旧 SSE、`turn_runner`、`live_turns`、`useHarnessTurn`、旧 reducer。
7. **多 tab 不是多连接**：切换 tab 不 detach、不 abort；停止按钮显式调用 `interrupt`。

---

## 2. OpenCode 是怎么实现 Web 聊天框的

### 2.1 同一 Server，多个客户端

OpenCode 的 Web UI 与 TUI 共用同一个 Server。Server 拥有：

- 会话、消息、inbox、权限、插件、模型与执行；
- 所有 provider 调用与工具执行；
- 认证、CORS、事件流。

Web/TUI/Desktop 都只是客户端。客户端不直接跑 Agent，也不维护另一套会话状态。

### 2.2 会话是持久聚合

`Session.Info` 至少包含：

- `id`、`parentID`、`projectID`、`location`；
- `agent`、`model`、`title`；
- `cost`、`tokens`；
- `time.created / updated / busy / idle / viewed / archived`；
- `outcome: succeeded | failed | interrupted`；
- `revert` 等。

一个会话同一时刻最多一个前台执行；多个客户端看到的执行状态一致。

### 2.3 发送消息不是“流式响应”

`POST /api/session/{sessionID}/prompt` 的职责是 **durably admit one session input and schedule agent-loop execution**：

请求主要字段：

```json
{
  "id": "client-message-id",
  "text": "hello",
  "files": [],
  "agents": [],
  "skills": [],
  "metadata": {},
  "delivery": "steer | queue",
  "resume": true
}
```

响应是 inbox item，不是 token 流：

```json
{
  "data": {
    "type": "user",
    "payload": { "text": "hello" },
    "delivery": "steer"
  }
}
```

真正的模型调用、工具调用、文本增量全部走事件流。`resume=false` 时只投递、不自动调度执行。

### 2.4 全局事件流是 live-only

客户端订阅：

```text
GET /api/event?types=session.text.delta,session.tool.success,...
```

SSE 帧：

```json
{
  "id": "event-id",
  "event": "session.text.delta",
  "data": { "...V2Event..." }
}
```

事件信封：

```json
{
  "id": "evt_...",
  "created": 1730000000000,
  "type": "session.text.delta",
  "location": { "directory": "/workspace" },
  "durable": { "aggregateID": "ses_...", "seq": 42, "version": 2 },
  "data": {
    "sessionID": "ses_...",
    "assistantMessageID": "msg_...",
    "ordinal": 0,
    "delta": "hel"
  }
}
```

关键事实：

- 事件流**没有历史回放**；断线期间的事件会丢。
- Web 客户端重连后靠 REST 重新拉取 `session.message.list`、`session.inbox`、权限/状态来恢复，而不是重放事件日志。
- delta 类事件是 volatile（没有 `durable`）；开始/结束/工具结果类事件是 durable，带 `aggregateID + seq`。
- 过滤在客户端共享连接内完成；`server.connected` 始终下发，用于触发 resync。

### 2.5 消息和 part

消息类型（`Session.Message.Info`）包括：

- `user`
- `assistant`
- `synthetic`
- `system`
- `skill`
- `shell`
- `compaction`
- `idle`
- `agent-switched`
- `model-switched`
- `location-switched`

`assistant` 的 `content` 是 part 数组：

- `text`
- `reasoning`
- `tool`

工具 part 的状态：

- `streaming`：参数还在流式生成，`input` 为字符串；
- `running`：已解析参数，正在执行；
- `completed`：有 `content` 与 `metadata`；
- `error`：有结构化错误与 `content`。

工具事件细化到输入流：

- `session.tool.input.started`
- `session.tool.input.delta`
- `session.tool.input.ended`
- `session.tool.called`
- `session.tool.progress`
- `session.tool.success`
- `session.tool.failed`

文本/推理也分 started/delta/ended，并以 `assistantMessageID + ordinal` 定位 part。

### 2.6 队列、steer、interrupt

- `delivery: "queue"`：等当前执行结束再投递。
- `delivery: "steer"`：在下一个 step boundary 插入当前执行。
- `GET /api/session/{id}/inbox`：列出尚未投递的 durable 工作项。
- `DELETE /api/session/{id}/inbox/{inboxID}`：取消尚未投递的项。
- `POST /api/session/{id}/interrupt?resume=true|false`：中断当前执行；`resume=true` 继续处理 steering/control，`queue` 项仍排队。
- `session.execution.interrupted` 的 reason 包括 `user | shutdown | superseded | inactivity`。
- 中断后未完成的工具调用会被报告为失败，不会假装成功。

### 2.7 权限、表单、问题

- `permission.asked` / `permission.replied` 是全局事件。
- `POST /api/session/{id}/permission/{requestID}/reply`，decision 为 `once | always | reject`。
- 权限请求带 `action`、`resources`、`metadata`、`source`，而不是只带一个 path。
- 表单/问题走 `form.created / form.replied / form.cancelled` 与 reply 端点；工具 `question` 可以要求客户端渲染结构化表单。

### 2.8 其它会话操作

OpenCode 把这些都做成独立操作 + 事件，而不是混在聊天流里：

- compact：`POST /session/{id}/compact`，默认 `steer`，在下一 step boundary 执行；有 `session.compaction.started/delta/ended/failed`。
- revert：stage → commit / clear；stage 可按 message id 形成可逆边界，并可应用文件回滚。
- fork：按 message id 复制历史到子会话；有 `session.forked`。
- model/agent switch：`session.model.selected`、`session.agent.selected`。
- usage：`session.usage.updated`、`session.usage.recorded`。
- rename/archive/move/delete/fork 都有 durable 事件。
- message list 支持 `limit/order/cursor/type` 分页。

### 2.9 前端如何从状态推导 UI

OpenCode Web 的聊天框本质上是：

```text
REST hydration: session + messages + inbox + permissions + status
        +
global SSE: incremental part mutations
        =
normalized UI state
```

- 先拿消息快照，再应用 live delta；part id/ordinal 让重复应用幂等。
- 用户消息可以乐观展示，但以 `inbox.enqueued`/`delivered` 和服务端消息为准。
- 工具行只读取 part 状态，不在 UI 里重新推导生命周期。
- 断线后重新 hydration，而不是依赖事件日志补洞。

---

## 3. AgentSuper 现状与差距

| 维度 | 当前 AgentSuper | OpenCode 目标 |
| --- | --- | --- |
| 发送 | `POST /chat/multi-agent/stream` 直接返回该轮 SSE | `POST prompt` 只 admission，返回 inbox item |
| 事件连接 | 每个回合/会话一个 SSE，有 `turn_id/seq` 回放 | 一条全局 `/event`，live-only |
| 断线恢复 | 重放 turn 事件日志，最多保留 60s | 重拉 messages/inbox/status，事件不回放 |
| 执行单元 | `TurnRunner` 按 turn 组织 | 会话级 execution + durable inbox |
| 队列 | `sess.prompts`，前端队列 | durable inbox，`steer/queue` |
| 消息模型 | `message.agents[].parts` + Harness 事件 | `Message + content Part[]` |
| 工具 | `agent_step` 投影 | tool part + 输入/执行/进度/结果事件 |
| 权限 | 回合内 `permission_request` | 全局 `permission.asked/replied` + always |
| 表单/问题 | 无 | form/question 事件与 reply |
| 压缩/revert/fork | 有底层能力，但事件与 UI 不完整 | 独立操作 + 完整事件 |
| 重试 | 前端自动重试 | 服务端 `session.status=retry` + `session.retry.scheduled` |
| 多 tab | tab 只有路由；每个会话各自维护流 | tab 只是视图；全局连接 + 会话 runtime |
| 状态恢复 | 依赖 turn 日志与 live_turns sidecar | messages/inbox/status 为真相 |

当前代码里需要被替换或删除的核心点：

- `backend/app/turn_runner.py`、`backend/app/live_turns.py`
- `backend/app/api/compat.py` 中的 `multi-agent/stream` 与重放逻辑
- `frontend/src/harness/useHarnessTurn.ts`、`adapter.ts`、`reducer.ts`、`useTimelineStream.ts`
- `frontend/src/api/multiAgent.ts` 的 turn SSE 传输
- `store.multiAgent` 中按 agents/steps 拼装消息的部分

---

## 4. 目标架构

### 4.1 总体结构

```text
Browser tabs / Mobile / TUI
        │
        ├── REST: session / messages / inbox / permission / form / compact / revert / fork
        └── SSE:  GET /api/v2/event  (one connection per browser)
                    │
             Event Router
                    │
        ┌───────────┴───────────┐
        │                       │
 Session Runtime           Global Stores
 (per session)            (models, config, permissions, forms)
        │
 Execution Coordinator
        │
 Agent Loop / Tools / Model Runtime
        │
 Projector: message + part -> SQLite
        │
 Event Outbox / Emitter
```

### 4.2 后端模块

新增或重构为：

- `backend/app/session/runtime.py`：每会话运行时；持有一个执行、inbox、取消信号、状态。
- `backend/app/session/inbox.py`：durable admission、delivery、promote、cancel。
- `backend/app/session/execution.py`：执行协调器；idle → busy → idle/retry；step boundary 插入 steer。
- `backend/app/session/projector.py`：把执行结果写成 message/part；不再让 UI 从 turn 事件拼装。
- `backend/app/events/bus.py`：全局事件总线；durable 事件写 outbox；volatile 直接广播。
- `backend/app/events/contracts.py`：事件 schema、版本、序列化。
- `backend/app/api/v2/`：session、prompt、message、inbox、event、permission、form、compact、revert、fork。
- `backend/app/session/repository.py`：message/part/inbox 的原子读写。
- `backend/app/session/coordinator.py`：跨会话并发上限、启动对账、恢复。

保留但下沉：

- `models/runtime.py`、`execution/tool_registry.py`、`harness/compression.py`、`cognition/context_meter.py` 继续复用。
- LangGraph 可以保留为内部执行器，但事件只能由 projector/part 层发出，不能直接把图事件透传给前端。

### 4.3 数据模型

#### 会话

在现有 `sessions` 基础上补齐：

- `status`: `idle | busy | retry`
- `outcome`: `succeeded | failed | interrupted | null`
- `time_busy`、`time_idle`、`time_viewed`
- `model`、`agent`（会话级当前选择）
- `cost`、`tokens_*`
- `revert_state`（staged boundary）

#### 消息

`session_messages` 保留，但 `data` 只存消息元数据，不重复存 content：

- `id`、`session_id`、`type`、`time_created`、`time_completed`
- `data`: agent/model/finish/raw_finish/cost/tokens/error/metadata

#### Part

`message_parts` 扩展为：

- `id`（稳定 part id 或 `messageID:ordinal`）
- `session_id`、`message_id`
- `ordinal`
- `type`: `text | reasoning | tool | file | ...`
- `data`: 文本、工具状态、输入、输出、错误、native
- `time_created`、`time_updated`

约束：`unique(session_id, message_id, ordinal)`；tool part 另用 provider tool call id 保证幂等。

#### Inbox

`session_inputs` 升级为 inbox：

- `id`、`session_id`
- `type`: `user | synthetic | compaction | move`
- `payload`（text/files/agents/skills/metadata）
- `delivery`: `steer | queue`
- `status`: `pending | delivered | cancelled`
- `admitted_seq`、`promoted_seq`、`time_created`、`time_delivered`

#### 其它

- `permissions`：request/reply/saved（once/always）。
- `forms`：created/replied/cancelled。
- `event_outbox`：可选；只用于 durable 事件审计与多进程，不作为客户端历史回放。
- `revert`：staged boundary、affected files。
- `snapshots`：复用现有 snapshot。

### 4.4 API v2

统一前缀：`/api/v2`。返回体保持 `{ "data": ... }`。

| Method | Path | 语义 |
| --- | --- | --- |
| POST | `/session` | 创建会话，允许客户端传 `id`、`title`、`agent`、`model`、`location`、`parentID`、`metadata` |
| GET | `/session` | 列表；`limit/order/search/parentID/archived/directory/cursor` |
| GET | `/session/{id}` | 会话详情 + usage + status |
| PATCH | `/session/{id}` | rename/metadata/archive |
| DELETE | `/session/{id}` | 删除，级联子会话 |
| POST | `/session/{id}/prompt` | durable admission，返回 inbox item |
| GET | `/session/{id}/inbox` | 未投递项 |
| PATCH | `/session/{id}/inbox/{inboxID}` | 改 delivery |
| DELETE | `/session/{id}/inbox/{inboxID}` | 取消 |
| POST | `/session/{id}/interrupt` | `resume=true/false` |
| GET | `/session/{id}/message` | 分页消息；`limit/order/cursor/type` |
| GET | `/session/{id}/message/{messageID}` | 单条消息 |
| POST | `/session/{id}/compact` | 手动压缩；默认 steer |
| POST | `/session/{id}/revert/stage` | 按 message id 建边界 |
| POST | `/session/{id}/revert/commit` | 提交回滚 |
| DELETE | `/session/{id}/revert` | 清除 staged |
| POST | `/session/{id}/fork` | 按 `before` 复制历史 |
| POST | `/session/{id}/model` | 切换会话模型 |
| POST | `/session/{id}/agent` | 切换会话 Agent |
| GET | `/session/{id}/permission` | 待处理权限 |
| POST | `/session/{id}/permission/{requestID}/reply` | `once/always/reject` |
| GET | `/session/{id}/form` | 表单列表 |
| POST | `/session/{id}/form/{formID}/reply` | 表单回复 |
| GET | `/event` | 全局 SSE；`types` 过滤 |
| GET | `/session/active` | 活跃会话（可选） |

#### Prompt 请求

```json
{
  "id": "msg-client-id",
  "text": "分析这个仓库",
  "files": [
    { "type": "file", "uri": "file:///workspace/a.ts", "mime": "text/plain" }
  ],
  "agents": [],
  "skills": [],
  "metadata": { "source": "web" },
  "delivery": "steer",
  "resume": true
}
```

规则：

- `id` 必填或由客户端生成；重复 id 返回原 admission，不重复执行。
- 请求成功不代表回答完成；只代表已持久加入 inbox。
- `resume=false` 用于“预投递但不启动”。
- `delivery` 缺省为 `steer`。
- 已有会话的 directory 不可修改；新会话才允许 location。

#### 事件 SSE

```text
GET /api/v2/event?types=session.text.delta,session.tool.success,...
Content-Type: text/event-stream
```

连接建立后第一条始终是：

```text
event: server.connected
data: { "type": "server.connected", ... }
```

客户端收到 `server.connected` 后执行 resync。

### 4.5 事件契约

信封固定为：

```ts
interface EventEnvelope<T> {
  id: string
  created: number
  type: string
  location?: { directory: string; projectID?: string }
  durable?: { aggregateID: string; seq: number; version: number }
  data: T
}
```

必须实现的事件族：

**会话生命周期**

- `session.created`
- `session.renamed`
- `session.archived` / `session.unarchived`
- `session.moved`
- `session.deleted`
- `session.forked`
- `session.viewed`
- `session.updated`（可选，聚合变更）

**执行/状态**

- `session.execution.started`
- `session.execution.succeeded`
- `session.execution.failed`
- `session.execution.interrupted`
- `session.status`
- `session.idle`
- `session.retry.scheduled`

**Inbox**

- `session.inbox.enqueued`
- `session.inbox.delivered`
- `session.inbox.cancelled`
- `session.inbox.delivery.changed`

**Step / 文本 / 推理**

- `session.step.started`
- `session.step.streamed`
- `session.step.ended`
- `session.step.failed`
- `session.text.started`
- `session.text.delta`
- `session.text.ended`
- `session.reasoning.started`
- `session.reasoning.delta`
- `session.reasoning.ended`

**工具**

- `session.tool.input.started`
- `session.tool.input.delta`
- `session.tool.input.ended`
- `session.tool.called`
- `session.tool.progress`
- `session.tool.success`
- `session.tool.failed`

**权限/表单**

- `permission.asked`
- `permission.replied`
- `form.created`
- `form.replied`
- `form.cancelled`

**压缩**

- `session.compaction.started`
- `session.compaction.delta`
- `session.compaction.ended`
- `session.compaction.failed`

**其它**

- `session.message.content.updated`
- `session.usage.updated`
- `session.usage.recorded`
- `session.synthetic`
- `session.skill.activated`
- `session.shell.started` / `session.shell.ended`
- `session.revert.staged` / `session.revert.committed` / `session.revert.cleared`
- `session.model.selected`
- `session.agent.selected`
- `server.connected`

规则：

- durable 事件必须带 `aggregateID + seq + version`；同一会话 seq 单调递增。
- volatile delta 不带 durable；允许丢失，靠 REST hydration 修复。
- 事件发送顺序必须与 projector 提交顺序一致；先落库，再发事件。
- 不允许同一语义同时发 `agent_step` 和 part 事件。

### 4.6 执行与投递

```text
prompt
  -> validate + resolve location
  -> insert inbox item (status=pending)
  -> emit inbox.enqueued
  -> coordinator.wake(session)
      if idle:
        mark busy; emit execution.started
        loop:
          pick next deliverable inbox item
          mark delivered; emit inbox.delivered
          create/append assistant message
          run step (model/tool loop)
          at step boundary: promote steer items
        mark idle/succeeded; emit execution.succeeded/session.idle
      if busy:
        leave pending; delivery decides order:
          queue: after current execution
          steer: next step boundary
```

要点：

- 一个会话最多一个执行；跨会话受全局并发限制。
- 取消信号在工具执行和模型流式之间都要可中断。
- 中断后未完成 tool part 置为 error；message idle outcome=`interrupted`。
- `resume=true` 只继续 steering/control，不自动消费 queue。
- 后台执行不依赖任何浏览器连接。

### 4.7 投影与持久化

执行过程中：

1. 产生 part 变更；
2. projector 在事务中写 `session_messages` / `message_parts`；
3. 提交成功后发事件；
4. 前端收到事件只更新对应 part。

禁止：

- 先发事件再落库；
- UI 根据多个事件自行拼出工具参数；
- 同一 part 在消息 JSON 和 part 表各存一份；
- 用 `time_created` 当 part 顺序（必须用 `ordinal`）。

### 4.8 前端分层

```text
api/client.ts                  REST typed client
api/eventStream.ts             one shared SSE connection
stores/sessionRuntime.ts       per-session normalized state
stores/tabs.ts                 tab metadata only
stores/global.ts               models/agents/config/permissions/forms
features/chat/eventReducer.ts  event -> state
features/chat/Timeline.vue     message + part renderer
features/chat/Composer.vue     prompt/inbox/attachments
```

`sessionRuntime` 结构建议：

```ts
interface SessionRuntime {
  id: string
  info: SessionInfo | null
  status: 'idle' | 'busy' | 'retry'
  messageOrder: string[]
  messages: Record<string, Message>
  partsByMessage: Record<string, string[]>
  parts: Record<string, Part>
  inbox: InboxItem[]
  permissions: PermissionRequest[]
  forms: Form[]
  usage: Usage
  hydrated: boolean
  lastEventSeq?: number
}
```

### 4.9 多 tab 规则

- tab = `{ id, kind: 'session' | 'page', path, title, sessionId? }`。
- 会话 tab 只持有 session id；消息、流、权限都在 `sessionRuntime`。
- 全浏览器一个 SSE 连接；所有会话事件按 `sessionID` 路由。
- 切换 tab：只切换可见视图，不 detach、不 abort、不重新 hydration（除非 `hydrated=false`）。
- 关闭 tab：不删除会话、不中断执行；只删视图。删除会话必须走显式菜单。
- 新建会话：客户端生成 session id，先创建 tab 和轻量 session info；首次 prompt 或显式 create 时落库。
- 会话 tab 标题由 `session.renamed` / message 首句更新。
- tab 上显示状态点：busy 呼吸、retry 黄色、权限待处理红色。
- 启动恢复：从 localStorage 恢复 tab 元数据；只对当前激活 tab 立即 hydration；其余 tab 懒加载。
- 移动端复用同一 runtime，不另建状态模型。

### 4.10 重连与恢复

标准流程：

```text
1. 打开 /api/v2/event
2. 等待 server.connected
3. 对活跃/可见会话：
   GET /session/{id}
   GET /session/{id}/message?order=asc
   GET /session/{id}/inbox
   GET /session/{id}/permission
4. 用快照替换本地 runtime
5. 应用 hydration 期间缓冲的事件
6. 若事件流断开：关闭旧连接，重连，重复 1-5
```

原则：

- 不重放事件历史。
- `hydrated=false` 前不渲染“空会话”，显示骨架。
- 快照与事件都按 message id / part id / ordinal 幂等合并。
- 断线不取消后台执行；只有显式 `interrupt` 才取消。
- 若 SSE 持续失败，tab 上显示“连接中断/重试中”，不把执行标成失败。

### 4.11 错误与重试

- 错误统一为 `StructuredError`：`type`、`message`、`status`、`retryable`、`provider`、`code`。
- 服务端负责可重试判断和调度；发 `session.retry.scheduled`，`session.status=retry`。
- 前端只展示倒计时/重试状态，不再自行 `setTimeout` 重发。
- `session.execution.failed` 是终态；`session.idle` 的 outcome 与之一致。
- 用户中断产生 `execution.interrupted`，不是 `failed`。

### 4.12 权限与表单

- 权限请求进入全局 `permission.asked`，按 sessionID 路由到 runtime。
- 聊天内联卡展示 action/resources/source/message/diff。
- 回复 `once | always | reject`；`always` 写 saved permissions。
- 一个会话可以同时有多个权限请求；UI 队列化，不覆盖。
- 中断/删除会话时，待处理权限自动 reject 并关闭。
- `question` / form 走 form 卡；用户提交后 `form.replied`，工具继续执行。

### 4.13 压缩、revert、fork

- 压缩是 inbox control item，不是普通聊天行；聊天流里显示可折叠 compaction 卡。
- 压缩期间保留当前可见消息，结束后用新的 context summary 影响下一轮。
- revert 只允许在 idle 或由服务端排队；staged 状态展示“将回滚到此处”。
- fork 创建新 session tab；原 tab 不关闭；fork 边界显示在时间线。
- 三者都发 durable 事件，刷新后可恢复 UI。

### 4.14 附件

- 前端先上传到会话工作区，拿到稳定文件引用。
- prompt 只传引用；消息 part 保存文件名、mime、大小、提取状态。
- 图片走多模态；文档走 `.extracted.txt` + `read_file`。
- 失败的上传在 composer/inbox 中可见并可重试；不阻塞其他附件。

### 4.15 子 Agent

- 子 Agent 是 `parentID` 子会话；`task` 工具触发 `session.created`（parent）。
- 父时间线显示 task 工具 part；展开可进入子会话（新 tab 或抽屉）。
- 子会话有独立 runtime 和状态；不自动占用主 tab。
- 子会话结束发 `session.idle`；父工具 part 收结果。

---

## 5. 状态机

### 5.1 会话状态

```text
idle --prompt--> busy --success--> idle
busy --error retryable--> retry --success--> idle
retry --fail--> idle(failed)
busy --interrupt--> idle(interrupted)
busy --fatal--> idle(failed)
```

### 5.2 Inbox 项

```text
pending --deliver--> delivered
pending --cancel--> cancelled
pending --change delivery--> pending
```

`steer` 与 `queue` 只决定投递时机，不改变 pending 状态。

### 5.3 工具 part

```text
streaming --input.ended/called--> running
running --progress--> running
running --success--> completed
running --failed--> error
running --interrupt--> error(interrupted)
```

### 5.4 文本/推理 part

```text
(started) --delta*--> ended
```

live 阶段只有 started/delta；`ended` 携带最终全文，用于校正丢失的 delta。

### 5.5 Tab 状态

```text
open --activate--> active --switch--> inactive
active --close--> adjacent/home
session busy/retry/permission 只影响 badge，不影响 tab 生命周期
```

---

## 6. 关键时序

### 6.1 空闲时发送

```text
UI: optimistic inbox item
POST prompt
  -> inbox.enqueued
  -> execution.started
  -> inbox.delivered
  -> step.started
  -> text.started/delta.../ended
  -> tool.input.*/called/progress/success
  -> step.ended
  -> execution.succeeded
  -> session.idle
UI: user message confirmed + assistant parts finalized
```

### 6.2 执行中发送

```text
POST prompt (delivery=queue)
  -> inbox.enqueued
  -> UI 显示“排队中”
当前执行结束 -> inbox.delivered -> 新 execution
```

`delivery=steer` 时在下一个 step boundary 交付，不等待整个执行结束。

### 6.3 刷新/重连

```text
SSE connect -> server.connected
hydrate session/messages/inbox/permission
buffer live events
replace runtime
apply buffer
```

### 6.4 切换 tab

```text
保存当前滚动位置/草稿
切换 visible session id
若 runtime.hydrated=false: hydrate
不关闭 SSE，不 abort，不重置消息
```

### 6.5 权限

```text
permission.asked -> runtime.permissions.push
UI inline card -> POST reply
permission.replied -> remove card
工具继续/失败
```

### 6.6 压缩

```text
POST compact (steer)
inbox.enqueued(compaction)
compaction.started
compaction.delta*
compaction.ended
compaction message persisted
next model call uses summary
```

### 6.7 Revert / Fork

```text
revert/stage -> revert.staged -> UI boundary
revert/commit -> revert.committed -> messages hidden
fork -> session.forked -> new session + tab
```

---

## 7. 全特性清单

| 特性 | API / 事件 | 目标行为 |
| --- | --- | --- |
| 会话创建 | `POST /session` | 客户端可给 id；tab 立即出现 |
| 会话列表/搜索/分页 | `GET /session` | 支持 directory/search/archived/cursor |
| 会话重命名/归档/移动/删除 | `PATCH/DELETE` + events | UI 即时更新，其他 tab 同步 |
| 会话 fork | `POST /session/{id}/fork` | 新 tab，按 message 边界复制 |
| 发送文本 | `POST prompt` | durable admission，不是流 |
| 附件 | prompt.files | 图片/文档/引用；上传失败可重试 |
| Agent/Skill mention | prompt.agents/skills | 按 OpenCode 的 admission 语义 |
| delivery steer/queue | prompt.delivery | 忙碌时可选插队或排队 |
| inbox 查看/取消 | inbox API + events | 排队项可见、可取消 |
| interrupt | interrupt API | 停止当前执行；不关 tab |
| resume | interrupt?resume=true | 继续 steer/control，不消费 queue |
| 文本流 | text.* | part 增量，ordinal 幂等 |
| 推理流 | reasoning.* | 可折叠展示，不混入答案 |
| 工具输入流 | tool.input.* | 参数生成中也能展示 |
| 工具执行/进度/结果 | tool.called/progress/success/failed | part 状态机 |
| 工具错误 | tool.failed | 结构化错误 + 可继续对话 |
| 权限 | permission.asked/replied | once/always/reject，内联卡 |
| 表单/问题 | form.* | 结构化表单，不阻塞其他 tab |
| 压缩 | compact + compaction.* | 自动/手动，可折叠卡 |
| revert | revert/stage/commit/clear | 按消息回滚，可恢复 |
| 模型切换 | model endpoint/event | 会话级选择，切换前压缩探测 |
| Agent 切换 | agent endpoint/event | 会话级选择 |
| usage/cost/tokens | usage events | 顶栏/详情/侧栏一致 |
| 消息分页 | message.list cursor | 长会话按页加载 |
| 消息类型 | user/assistant/system/synthetic/skill/shell/compaction/idle | 时间线统一渲染 |
| 子 Agent | session.created(parentID) | 子会话可进入，不占默认 tab |
| 重试 | status=retry + retry.scheduled | 服务端调度，UI 倒计时 |
| 断线恢复 | server.connected + hydration | 不靠事件回放 |
| 多 tab | tabs store + global SSE | 状态实时、互不打断 |
| 移动端 | 同一 runtime | 复用，不另写状态机 |
| 错误分类 | StructuredError | retryable/status/code/provider |
| 认证/CORS | Server config | Web/TUI 同源；SSE 同鉴权 |

---

## 8. 重构阶段（禁止补丁）

### Phase 0：契约冻结

交付：

- 本文档 + 事件/HTTP schema；
- Pydantic 与 TypeScript 类型；
- JSON fixtures（session/message/part/inbox/event）；
- reducer 属性测试骨架。

Gate：任何 UI/后端实现不得先于类型与 fixture。

### Phase 1：持久层 v2

交付：

- sessions 状态字段；
- message/part 规范化（ordinal、part id、tool state）；
- inbox 表与 delivery；
- permission/form 表；
- 迁移脚本与双读校验（只校验，不双写）。

Gate：旧会话可无损迁移；message/part 重建模型视角测试通过。

### Phase 2：API v2 + 执行协调器

交付：

- `/api/v2/session*`；
- prompt admission；
- inbox delivery；
- execution coordinator；
- interrupt/compact/revert/fork；
- projector 落库。

Gate：API contract tests；无浏览器参与也能完成一轮执行并落库。

### Phase 3：事件总线

交付：

- `/api/v2/event`；
- durable outbox + volatile broadcast；
- 全部事件族；
- SSE 过滤、背压、连接生命周期。

Gate：事件 schema tests；乱序/重复/断线测试；投影与事件顺序一致。

### Phase 4：前端 v2

交付：

- REST client；
- 单 SSE 连接；
- sessionRuntime；
- tabs v2；
- Timeline/Composer/Permission/Form；
- 重连 hydration。

Gate：多 tab 同时流式、刷新恢复、切 tab 不中断。

### Phase 5：功能对齐

交付：

- queue/steer UI；
- retry 倒计时；
- compaction/revert/fork UI；
- usage；
- 子会话；
- 附件。

Gate：第 7 节清单逐项验收。

### Phase 6：删除旧实现

删除：

- `turn_runner.py`、`live_turns.py`
- `/chat/multi-agent/stream` 与重放
- `useHarnessTurn`、`adapter`、`reducer`、旧 timeline stream
- 旧 queue 字段/接口
- 旧文档与测试

Gate：全量测试 + 移动端 + 真实模型 smoke；旧端点 410/404，不再双轨。

---

## 9. 测试与验收

### 9.1 契约测试

- 所有 HTTP 请求/响应 JSON schema；
- 所有事件信封与 data schema；
- durable 事件 seq 单调；
- prompt id 幂等。

### 9.2 Reducer 属性测试

- 任意事件顺序、重复、缺失 delta；
- text/reasoning ordinal 幂等；
- tool streaming → running → completed/error 合法转换；
- hydration + buffer 合并；
- 断开/重连不产生重复消息。

### 9.3 集成测试

- idle 发送；
- busy queue / steer；
- interrupt + resume；
- 权限 once/always/reject；
- form 回复；
- compact 自动/手动；
- revert stage/commit/clear；
- fork；
- 多 tab 同时执行；
- 刷新/断网恢复；
- 移动端复用。

### 9.4 性能与稳定性

- 10k 文本 delta 不卡 UI；
- 10 个会话 tab 共用一个 SSE；
- 长会话分页加载；
- SSE 慢消费者背压；
- 进程重启后 active session 对账。

### 9.5 验收标准

1. 发送后立即得到 inbox 确认，不等待模型。
2. 关闭/切换 tab 不影响执行；刷新后能恢复。
3. 两个会话 tab 同时流式，互不串台。
4. 工具参数、执行、结果分阶段可见，刷新后一致。
5. 权限、表单、压缩、revert、fork 都有事件与 UI。
6. 断线后不靠事件回放也能恢复；无重复消息。
7. 后端重启后，未完成执行有明确 outcome，不悬挂。
8. 旧 turn-centric 代码全部删除。

---

## 10. 风险与待确认

1. **硬切还是版本化**：建议 `/api/v2` 硬切；旧端点只保留一个发布周期，随后删除。不要在新旧之间做长期适配层。
2. **事件是否持久化回放**：建议 durable 事件只做审计/多进程，不作为客户端回放源；客户端恢复靠 REST hydration。若要求“离线补齐”，再单独设计 event log，但不要恢复 turn log。
3. **表单/问题范围**：建议完整支持 `question` 工具与 form reply；若不支持，权限卡与表单卡会混在一起。
4. **LangGraph 去留**：建议保留为内部执行器，但禁止图事件直接透传；所有输出必须投影为 message/part。
5. **模型切换粒度**：建议会话级 model/agent；每次 prompt 不再隐式切模型。若保留“每轮模型”，作为 metadata 扩展，不进入 canonical 状态。
6. **子会话 tab**：默认不自动开 tab，只在用户展开 task 时打开；避免 tab 爆炸。
7. **历史迁移**：现有 `session_messages` 的 data 里含 content/tool_calls，需要一次性投影为 part；迁移必须可重复、可校验。
8. **移动端**：必须复用同一 sessionRuntime；不要在 MobileShell 维护第二套消息状态。

---

## 11. 实施记录

- Phase 0（契约冻结）：已完成。契约在 `backend/app/contracts/v2.py`、`frontend/src/contracts/v2.ts`；共享 fixtures 在 `contracts/v2/fixtures/`。
- Phase 1（持久层 v2）：已完成。`sessions` 增加 outcome/metadata/revert_state/时间戳；`session_messages.time_completed`；`message_parts.ordinal/time_updated` + 唯一索引；`session_inputs` 增加 type/status/time_delivered；新增 `session_permissions`、`session_permission_saved`、`session_forms`、`schema_migrations`。SQLite 迁移在 `backend/app/session/migrations.py`，MySQL/PostgreSQL 迁移为 `backend/alembic/versions/0004_web_chat_v2.py`。
- Phase 2（API v2 + 协调器）：已完成基础链路。`backend/app/api/v2/session.py` 提供 session/prompt/inbox/message/interrupt/compact/revert/fork/model/agent；`backend/app/session/coordinator.py` 负责 durable inbox 串行执行，复用 `PlatformContext.run`，并把 `final_output` 回写为 assistant 消息。
- Phase 3（事件总线 + SSE）：已完成基础链路。`backend/app/events/bus.py` 提供过滤/慢消费者关闭；`backend/app/events/emitter.py` 负责 durable outbox 与事件发布；`GET /api/v2/event` 提供 `server.connected`、类型过滤、心跳；会话/执行/inbox/usage/compaction/revert/fork/model/agent 事件已接线。
- Phase 4（前端 v2 基础）：已完成 API client、单连接 SSE、sessionRuntime/reducer。`frontend/src/api/v2.ts`、`frontend/src/v2/eventStream.ts`、`frontend/src/v2/runtime.ts`、`frontend/src/v2/sessionStore.ts`；含事件幂等、增量 text/reasoning/tool 合并、inbox/permission/form/status 更新、hydration 缓冲。视图层（Timeline/Composer）在 Phase 5 接入。
- Phase 5（v2 视图，部分完成）：`/multi-agent`、`/multi-agent/:id` 与 `/v2/chat` 均使用 `V2ChatView`；桌面与移动端共用。支持时间线、Composer、附件、语音消息、inbox、权限/表单卡、停止/压缩/撤销/fork、模型切换、文件改动 diff/还原。`PlatformContext.stream_turn_events` 复用图事件队列，coordinator 已接实时 text/tool 事件并记录轮次快照。旧 `MultiAgentView` 仅保留给测试/回滚。
- Phase 6（完成）：已删除前端旧 `MultiAgentView`、`ConversationTimeline`、`TimelineToolRow`、`MultiAgentResponse`、`AgentPanel`、`PermissionInlineCard`、`ChatInput`、`harness/` 旧传输层及旧 store 的 `send/resume/attachTurn`；后端已删除 `live_turns`、`turn_runner`、旧 `/chat/multi-agent/stream` 及旧 stream 测试，并迁移队列/usage/回放覆盖到 v2。侧栏压缩/分叉已切到 `/api/v2`，轮次快照落 `session_snapshots` 表，重启后仍可 diff/还原。
- 真实模型验收：新增 `backend/tests/integration/test_v2_real_features.py`（env-gated），覆盖文件系统写入/读取、手动压缩、权限拒绝。运行命令：`HARNESS_REAL_TEST=1 HARNESS_REAL_MODEL=deepseek/deepseek-v4-flash pytest tests/integration/test_v2_real_features.py -q -s`。