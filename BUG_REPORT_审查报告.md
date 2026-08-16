# BUG / 风险审查报告

> 审查范围：`backend/app/agent/graph.py`、`backend/app/agent/supervisor.py`、`backend/app/agent/bus.py`、`backend/app/agent/stream_events.py`、`backend/app/api/chat.py`、`backend/app/session/repository.py`、`frontend/src/stores/multiAgent.ts`、`backend/app/runtime.py` 装配相关。
> 分级：【高】影响可用性 / 数据正确性；【中】并发/计数失真或边界；【低】代码质量 / 健壮性。

---

## 一、环境状态

| 项 | 状态 | 说明 |
|---|---|---|
| 端口 8000 | ✅ 当前可用 | 复查 `netstat` 无残留监听；上次 `uvicorn_err.log` 的 `[Errno 10048]` 系残留进程占用，现已释放。重新启动后端即可。 |

---

## 二、已确认问题

### 【高优】【环境】端口 8000 曾因残留进程被占用
- 现象：`uvicorn_err.log` 记录 `[Errno 10048]`，后端未启动，前端"助手连不上"。
- 处置：确认当前无占用。后续以 `.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload` 启动即可。
- 建议：启动前先 `netstat -ano | findstr :8000` 检查；可把端口改为可配置（`settings.port`）避免与其它服务冲突。

### 【中优】【并发】`api/chat.py` `_queue_counter` 排队计数失真
- 位置：`chat.py` `run_multi_agent`（现约 L399-413）。
- 问题：原实现 `sem.locked()` 时递增、进入信号量后**无条件** `max(0, _queue_counter - 1)` 递减。未排队（直接拿到 slot）的请求也会递减，导致等待请求的队列位置被提前清零、偏小；多请求并发下展示的"排队第几位"失真。
- 影响：仅前端 `queued.queue_position` 展示，不影响主流程。
- 修复（**已完成**）：用局部 `queued_position` 标记是否真正入队，只有入队过的请求才对称递减。

### 【低优】【代码质量】`supervisor.py::_synthesize` 无效 f-string
- 位置：`supervisor.py` L603。
- 问题：`lines = [f"以下是多个来源的信息汇总:\n"]` 无 `{}` 占位符，语法合法但属写错。
- 修复（**已完成**）：去掉 `f` 前缀。

### 【低优】【健壮性】`graph.py::_push_event` / `_push_stream_event` `put_nowait` 未兜底
- 位置：`graph.py` `_push_event`（约 L365）、`_push_stream_event`（约 L866）。
- 分析：当前装配下 eq 为**无界** `asyncio.Queue`，或 `AgentEventCollector` / `TaggedEventQueue`（其 `put_nowait` 内部已 try/except），实际不会抛 `QueueFull`——风险为理论性。但 `_execute_tool_streaming` 已有同款兜底，此处补上更一致。
- 修复（**已完成**）：两处均加 try/except 包裹。

### 【低优】【语义】`graph.py::_tool_task` 子请求 `source="user"`
- 位置：`graph.py` L593。
- 问题：委派子 Agent 时 `source` 写死 `"user"`，语义上应为主 Agent 自身 id。
- 影响：**功能无害**——`bus.send` 对 response/error 是"先查 `_pending` 按 thread_id 直接投递"，不依赖 `target`；只有延迟/广播链路（`target="*"` 排除 source）或链路追踪时该值才有语义偏差。
- 建议：后续若引入消息链路追踪，改为实际调用方 agent_id；当前无需修复。

### 【中性】`bus.py::send_and_wait` 超时后迟到回复被丢弃
- 位置：`bus.py` L175-180。
- 行为：超时后 `_pending.pop(thread_id)`；若子 Agent 后续才返回，response 走 `send()` 的 mailbox 路由（目标若为 `user` 则因非注册 Agent 触发 `Unknown target agent 'user'` 日志丢弃）。
- 判定：超时属预期场景，可接受。`agent_progress()` 已把"已完成步骤"回传给超时错误，具备可诊断性。

---

## 三、重点路径复核结论（599-987 行 + 相关模块）

### `graph.py` 工具循环关键路径 — 未见高优问题
- **工具执行**：`_execute_tool` 对 `tool_task` / `tool_memory_*` 特判前置，`tool_execute` 走流式并回退同步；`NeedsPermission` 走事件队列审批，无队列时立即拒绝而非永久等待（`chat.py` 直连 supervisor 的超时死锁已消除）。
- **步数上限**：生效上限 `min(MAX_TOOL_ROUNDS, MAX_STEPS)`；最后一轮注入 `MAX_STEPS_PROMPT`（assistant 角色）并禁用工具，对齐 opencode max-steps 语义。
- **doom-loop**：指纹 = 排序后的 `tool:args` 拼接，连续重复 `doom_loop_threshold` 轮注入策略提示，达 `doom_loop_max_strikes` 强制收尾。逻辑自洽。
- **token 预算**：每轮"清理旧工具输出 → 压缩(LLM) → 截断(兜底)"闭环在入口、主循环、强制收尾三条路径均存在；`tool_defs` 移入轮内、截断前先扣除 schema 预算。
- **finish_reason**：归一化映射 `tool_calls→tool-calls` 保持循环存活；`length`/`content-filter` 分别追加截断提示/转错误。对齐设计文档。

### `repository.py` 并发与事务边界 — 未见高优问题
- `append_message` 用 `BEGIN IMMEDIATE` + 单条 `INSERT … SELECT MAX(seq)+1`，seq 原子自增，无 read-then-write 竞态。
- `revert_to_message` 未显式 `BEGIN IMMEDIATE`，但整体受 `service.write_lock` 串行化保护；epoch 水位回滚逻辑正确（baseline_seq 不超过撤销点前的 compaction）。
- 连接池 `_get_db()/close()` 借贷模式在各函数中一致。

### 前端 `multiAgent.ts` 事件消费 — 与后端事件匹配
- `agent_start/agent_step/agent_done/agent_error/permission_request` 的消费逻辑与后端事件字段一致；`done` 后做客户端 genId → 服务器 id 的 key 迁移、墓碑去重，逻辑闭环。
- 流式期间 `loadConversation` 被 `streamPhase !== 'idle'` 短路，避免覆盖直播消息——设计如此，符合预期。

---

## 四、新增发现

### 【中优】【设计不一致】`tool_task` 子 Agent 的 memory namespace 与主 Agent 不一致 — ✅ 已修复
- 位置：`graph.py` `_tool_task`（原 payload `conversation_id: ""`）+ `memory.py` `get_by_tag`/`get` 的 namespace 过滤。
- 现象：主 Agent 记忆以 `namespace=session_id` 存取；经 `tool_task` 委派的子 Agent 以 `namespace=""` 运行，因此：
  - `tool_memory_search(tag)`：`get_by_tag` 在 `namespace=""` 时**跳过 namespace 过滤**，子 Agent 能搜到**所有会话**的标签记忆（全局泄露）；
  - `tool_memory_get(key)` / `set(key)`：走全局 key（无前缀），**读不到**主 Agent 以 `session_id` 前缀存的 key。
- 与 AGENTS.md"主 Agent 记住的信息子 Agent 也可检索到"的承诺**不一致**：search 过宽、get 过窄。
- 修复：`_execute_tool` → `_tool_task` 透传外层 `state.conversation_id`；`get_by_tag` 签名改为 `namespace: Optional[str] = None`——仅 `None` 才全局不过滤，空串 `""` 精确匹配空命名空间（子 Agent 不再泄漏全局记忆）。已用单测脚本验证三态语义。

### 【中低优】【设计】记忆 TTL 写死 5 分钟 — ✅ 已修复
- 位置：`graph.py` `_tool_memory` 原 `await mm.set(key, value, ttl=300, …)`。
- 影响：任何 `tool_memory_set` 的记忆 5 分钟后即过期，与"长期记忆"直觉不符；跨重启恢复也受 TTL 限制。用户此前 `tool_memory_search` 查不到多为"已过期 / 不同会话 / 标签不匹配"三者之一。
- 修复：新增配置项 `MEMORY_TTL_SECONDS`（默认 `300`，`config.py`），`_tool_memory` 改读 `settings.memory_ttl_seconds`。

### 【低优】【注释】`stream_events.py` 引用过时行号 — ✅ 已修复
- `stream_events.py` L130 注释原写 `graph.py:190-203`，已改为仅引用函数名（`_push_event`/`_push_stream_event`），避免行号漂移。

---

## 五、修复记录

| 文件 | 修复 |
|---|---|
| `backend/app/api/chat.py` | `_queue_counter` 仅对真正入队的请求递增/对称递减 |
| `backend/app/agent/supervisor.py` | `_synthesize` 回退分支去掉无效 `f` 前缀 |
| `backend/app/agent/graph.py` | `_push_event` / `_push_stream_event` 的 `put_nowait` 加 try/except 兜底 |
| `backend/app/agent/graph.py` | `tool_task` 透传 `conversation_id`，子 Agent 记忆与主 Agent 同 namespace |
| `backend/app/agent/memory.py` | `get_by_tag` 空串精确匹配空命名空间，消除全局泄漏 |
| `backend/app/agent/graph.py` | `tool_memory_set` 改用 `settings.memory_ttl_seconds` |
| `backend/app/config.py` | 新增 `MEMORY_TTL_SECONDS`（默认 300） |
| `backend/app/agent/stream_events.py` | 修复过时行号注释 |

全部通过 `py_compile`；namespace 语义用内存实例单测验证通过。

---

## 六、建议的下一步

1. 用 `.venv\Scripts\python.exe` 启动后端验证服务恢复（端口 8000 当前空闲）。
2. 需要更长记忆时，在 `.env` 设置 `MEMORY_TTL_SECONDS`（如 `86400`）。
3. 可选：把记忆工具与子 Agent 的共享语义写进 AGENTS.md（现已与实现一致：`tool_task` 子 Agent 与主 Agent 同一 `conversation_id` namespace，可互相检索）。
