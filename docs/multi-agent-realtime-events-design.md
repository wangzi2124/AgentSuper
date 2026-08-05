# Multi-Agent 实时执行事件展示设计（对齐 opencode session events）

## 1. 背景与问题

Multi-Agent Supervisor 目前是**黑盒式**交互：

- 前端 `/multi-agent/stream` 只收到 `queued / routing / done / error` 四种事件；
- 子 Agent（rag / web_search / code）在 `AgentBus` 内部异步执行，步骤、工具调用、失败原因全部不可见；
- 前端 `multiAgent.ts` store 虽然已经实现 `agent_start / agent_step / agent_stream / agent_done / agent_error` 的处理分支（`frontend/src/stores/multiAgent.ts:144-186`），`AgentStreamData` 也有 `steps: AgentStep[]` 字段（`frontend/src/types/index.ts:225-233`），但**后端从未发出这些事件**——UI 停留在"转圈 → 出结果"。

目标：把 opencode 的"任务执行实时展示"机制（`step.*` / `tool.*` / `agent.switched` 经 SSE 实时推送、前端按 agent 分组渲染时间线）移植到 AgentSuper 的 Multi-Agent 界面，让每个子 Agent 的开始、步骤、工具调用、完成/失败实时可见。

## 2. opencode 机制精读（参考基准）

opencode 的事件链路（`E:\project\opencode-dev\packages\opencode\src\`）：

```
core EventV2
  → event-v2-bridge.ts（publish 时经 InstanceRef/WorkspaceRef 附加 Location.Info）
  → bus/global.ts GlobalBus（Node EventEmitter，payload 自动补 id）
  → server/.../httpapi/handlers/event.ts（SSE：Queue.unbounded + 先注册监听再发 connected，按 directory/workspace 过滤）
  → TUI sdk.tsx（outbox 批量 emit）→ event.ts useEvent() → session-ui 组件
```

关键事件类型（`packages/schema/src/session-event.ts`）：

- `session.next.step.started/ended/failed`
- `session.next.tool.called/success/failed/progress`
- `session.next.text.delta/ended`、`session.next.reasoning.delta/ended`
- `session.next.agent.switched`、`session.next.model.switched`

语义要点：
- **步骤状态机**：`started → ended / failed`，同一步骤以 `stepID` 关联（前端 upsert 而非追加）；
- **工具事件**：`called → success/failed`，携带 `input`（参数）与 `output`（结果）；
- **delta 事件仅实时、不落库**（durable 事件才写 session）。
- 渲染层（`packages/session-ui/.../message-part.tsx`）：`PART_MAPPING` 按 part.type 分派；`tool` part 按 `state.status`（pending/running/completed/error）渲染，`BasicToolV2` 在运行中用 `TextShimmer` 呈现、可折叠展开参数/结果；文本 part 的 meta 显示 `AgentName · Model · Duration`。

对齐到 AgentSuper 的取舍：
- 不引入全局事件总线（Node EventEmitter）——AgentSuper 是同进程 asyncio，用**请求级事件队列 + payload 透传**即可，避免全局状态；
- 复用现有 `AgentStep` 模型（`step_start/step_end/tool_start/tool_end` + `step_id/status/detail/duration_ms/tool_name/tool_args/tool_result`），前端按 `step_id` upsert（对齐 opencode 的 stepID 关联）；
- `text.delta` 对齐为 `agent_stream`（RAG graph 目前不流式吐 token，仅当子 Agent 支持时使用，本期可选）。

## 3. AgentSuper 现状分析

### 3.1 执行链路（后端）

```
POST /api/chat/multi-agent/stream  (backend/app/api/chat.py:537)
  └─ run_multi_agent()  → agent_bus.send_and_wait(→ supervisor)
        supervisor (backend/app/agent/supervisor.py)
          ├─ _decompose()  → 关键词/LLM 拆解
          ├─ _route_to()     单任务：send_and_wait(→ 子 Agent)
          └─ _execute_parallel() 多任务：asyncio.gather(*run_one)
        RAGAgentWrapper (backend/app/agent/rag_wrapper.py)
          └─ RAGAgent.invoke(...)  (graph.py)
               └─ _push_event(state, ev)  # 已有点位：step_start/end、tool_start/end、tool_output/heartbeat、permission_request
```

关键事实：
- `RAGAgent.invoke()` **已支持** `event_queue: asyncio.Queue | None` 参数（`graph.py:786`），`_push_event` 会把事件 `put_nowait` 进该队列（`graph.py:190-203`）——但 wrapper 目前**没传**；
- `AgentMessage.payload` 是自由 dict，同一进程内存传递，**可以携带 `asyncio.Queue` 对象**；
- supervisor 的 `_route_to` 原样转发 `payload=payload`（`supervisor.py:158-167`），`_execute_parallel` 用 `sub_payload = dict(original_payload)` 浅拷贝（`supervisor.py:348-349`）——**payload 中的引用可一路透传到子 Agent**；
- `chat_multi_agent_stream` 已经创建了请求级 `event_queue: asyncio.Queue`（`chat.py:567`）供 `done/error` 事件使用；
- 持久化：`_persist_multi_agent`（`chat.py:363-384`）写主会话 + 子任务会话，assistant 消息 data 里有 `steps`/`sources`，**但没有按 agent 分组**——刷新页面后重放不出 agent 面板。

### 3.2 前端（已具备大部分处理逻辑）

- `stores/multiAgent.ts` 的 `send()` 已实现 `agentsMap`（`agent_id → AgentStreamData`）与 `agent_start/agent_step/agent_stream/agent_done/agent_error/done` 全部分支；
- `MultiAgentResponse.vue` 只渲染 `agents` 的头像/名字/状态徽标与最终文本，**未渲染 `steps`**；
- `types/index.ts` 的 `AgentStep` 字段与后端 `_push_event` 产出的事件字段一一对应，无需改模型；
- `api/multiAgent.ts` 的 `ConversationDetail.messages[].agents?: any[]` 已预留，store `loadConversation` 已映射 `agents: m.agents || []`。

**结论**：后端补事件、前端补渲染，其余复用。

## 4. 设计目标与范围

### 4.1 目标
1. `/multi-agent/stream` SSE 实时推送每个子 Agent 的 `agent_start / agent_step / agent_done / agent_error`；
2. 前端按 agent 面板展示实时步骤（状态图标、工具调用、可展开参数/结果、耗时），与单 Agent `StepTaskList.vue` 观感一致；
3. 执行结果按 agent 持久化到 session，刷新/重开历史后仍能重放 agent 面板与步骤。

### 4.2 不在本期范围
- `tool_output / tool_heartbeat` 高频噪音不推（避免 SSE 与 DB 膨胀）；
- `permission_request` 透传但前端不接审批 UI（沿用现状"等待超时→拒绝"语义，列为已知限制）；
- `text.delta / agent_stream`：RAG graph 不流式吐 token，暂不实现；
- 全局事件总线（opencode 式 GlobalBus）：同进程场景下 payload 透传更简单可靠。

## 5. 后端设计

### 5.1 新事件协议（SSE data 载荷）

| type | 字段 | 含义 |
| --- | --- | --- |
| `agent_start` | agent_id, agent_name, agent_avatar | 子 Agent 开始处理 |
| `agent_step` | agent_id, step: AgentStep | 步骤/工具事件（graph `_push_event` 原样包装） |
| `agent_done` | agent_id, content | 子 Agent 成功完成 |
| `agent_error` | agent_id, error | 子 Agent 失败/超时 |

`done` 事件新增 `agents: AgentStreamData[]`（兜底：前端漏事件时用快照回填，刷新后用持久化数据）。

### 5.2 新模块 `backend/app/agent/stream_events.py`

两个类 + 一组小函数（详见实现）：

```python
STEP_EVENT_TYPES = {"step_start", "step_end", "tool_start", "tool_end"}

class AgentEventCollector:
    """请求级收集器：转发到 SSE 队列 + 记录副本（供落库/兜底）。"""
    def put_nowait(event)          # 追加 events 副本 + 转发 SSE 队列
    def agents_snapshot() -> list  # 由 events 重建 AgentStreamData 快照
    def fail_running(message)      # 把仍 running 的 agent 标记 failed

class TaggedEventQueue:
    """RAG graph 事件队列适配器（只依赖 put_nowait 语义，graph 零改动）。
    step 类型 → 包装成 {type:"agent_step", agent_id, step}；
    permission_request → 原样透传；tool_output/tool_heartbeat → 丢弃。"""

def emit(queue, event)   # 空安全 push
def agent_meta(agent_id) # (中文名, 头像) 映射
```

`agents_snapshot` 的合并语义（对齐前端 store）：`agent_start` 建条目；`agent_step` 按 `step_id` upsert；`agent_done` 置 completed + content；`agent_error` 置 failed + error。

### 5.3 事件桥：payload 透传 `_event_queue`

`chat_multi_agent_stream`（chat.py）：

```python
event_queue: asyncio.Queue = asyncio.Queue()
collector = AgentEventCollector(event_queue)     # 新增
...
payload = {
    ...,
    "_event_queue": collector,                   # 透传到 supervisor → 子 Agent
}
```

supervisor 无需改动（`_route_to` 原样转发、`_execute_parallel` 浅拷贝均自动携带）。

### 5.4 各子 Agent 注入事件

**RAGAgentWrapper**（事件最丰富）：
- 处理 `chat` 动作开头：`emit(agent_start)`；
- `invoke(..., event_queue=TaggedEventQueue(collector, self._id))` —— graph 每个 `_push_event` 自动转为 `agent_step`；
- 成功后 `emit(agent_done, content=answer)`；异常分支 `emit(agent_error)`。

**WebSearchAgent / CodeAgent**（基础步骤）：
- `chat` 动作开头 `agent_start`，关键阶段打 `agent_step`（如 `search` / `synthesize` / `generate`），结束 `agent_done`，异常 `agent_error`。

### 5.5 会话持久化（重放支持）

- `_persist_multi_agent` 增加 `agents: list` 参数，assistant 消息 data 存 `"agents"`；
- `run_multi_agent` 在 `done` 前用 `collector.agents_snapshot()` 构建快照，落库 + 塞进 `done` 事件；
- `reply.type == "error"` / 超时 / 异常分支先 `collector.fail_running(msg)`，把还挂着的 agent 标失败。

### 5.6 单 Agent `/stream` 不受影响

`AgentEventCollector` 只加在 multi-agent 端点；单 Agent 路径（`agent_executor.py` 的 `register_request_queue`）保持原样。

## 6. 前端设计

### 6.1 store（`stores/multiAgent.ts`）

- `agent_step` 分支加守卫：`if (event.step && event.step.step_id)`（防非法 payload）；
- `done` 分支：若 `agentsMap` 为空且 `event.agents?.length`，用 `event.agents` 回填 `assistantMsg.agents`；
- 其余分支（`agent_start / agent_stream / agent_done / agent_error`）已有，复用。

### 6.2 `MultiAgentResponse.vue` 渲染步骤

- 每个 agent 卡片内，`status === 'running'` 时显示「执行中」并展示 `steps`；
- 步骤行复用 `StepTaskList.vue` 的视觉语言（`✅/⏳/❌` 图标、名称、detail、`🔧 tool_name` 参数摘要、`查看结果` 展开、耗时），抽成小组件 `AgentStepList.vue` 或内联模板；
- `step_id` 以 `tool_` 前缀开头的视为工具步骤（右侧显示 `tool_name`），其余为阶段步骤。

### 6.3 `MultiAgentView.vue`

- 新增对 `最后一条消息 agents[].steps.length` 的 watch，步骤实时增加时自动滚动到底部。

### 6.4 types（`types/index.ts`）

- `MultiAgentSSEEvent` 增加 `agents?: AgentStreamData[]`（`done` 事件携带）。

## 7. 边界与已知限制

1. **同名 agent 并发**：分解出两个相同 agent 的子任务时，前端按 `agent_id` 聚合会互相覆盖（现有架构限制，本期不做子任务级隔离）。
2. **权限请求**：multi-agent 下 `permission_request` 前端不弹审批，沿用"等待超时 → 拒绝"语义；与现状相比多 60s 等待（`PERMISSION_APPROVAL_TIMEOUT`），属低频场景，后续可在 multi-agent UI 接入审批面板。
3. **事件仅实时 + 快照落库**：`steps` 明细不单独落库（沿用现有 assistant `steps` 字段），agent 面板重放依赖 `agents` 快照，工具步骤重放粒度与实时一致（快照内含 steps）。
4. **`done.steps` 与 `agent_step` 重复**：`done` 事件仍携带原始 `steps`（供后端落库），前端 live 以 `agent_step` 为准，不重复渲染。

## 8. 验证

1. `py_compile` 全部改动后端文件；
2. 前端 `npm run build`（vue-tsc + vite）通过；
3. 运行后端 + 前端，Multi-Agent 提问（触发分解到多个 agent）：
   - SSE 依次收到 `agent_start → agent_step（retrieve/tool_*）→ agent_done`；
   - 前端各 agent 卡片实时更新步骤、状态徽标、参数/结果展开；
   - 完成后刷新页面，历史对话仍能重放 agent 面板与步骤。
