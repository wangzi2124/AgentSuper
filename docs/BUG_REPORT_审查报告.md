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

## 六、第三轮深度复核（graph / chat / repository / db / permission 全链路）

### N1【已证伪 — 代码正确】步数上限与收尾时机
- 证据：`effective_max_steps = min(max_tool_rounds, settings.max_steps)`，循环退出条件 `rounds < max_tool_rounds`（均 ≤ 轮数上限）。
- 结论：收尾提示最晚于 `max_tool_rounds-1` 轮注入并禁用工具（`final_tool_defs=None`），不会"提示收尾了还继续跑工具"。**上一轮怀疑不成立，撤回。**

### N4【已关闭】`_permission_denied_msg` 提示误导
- 证据：`_permission_denied_msg`（graph.py:85-93）明确写明"**这不是可重试的临时错误**"；无事件队列时直接拒绝而非永久等待。
- 结论：不会误导模型盲目重试。**关闭。**

### N5【确认存在 → ✅ 已修复】`IN (...)` 无分批拼接
- 位置：`repository.py` `list_parts_for_messages`、`revert_to_message` 的 `DELETE … IN (...)`。
- 风险：SQLite 变量上限默认 999，长会话（大量工具消息）加载历史/撤销时可能 `too many SQL variables`。影响面：`router.py:120`、`history.py:92/95`。
- 修复：新增 `_SQLITE_MAX_VARS = 500`，两处按批分片。已用 1000 条消息的临时 DB 实测通过（list=1000 → revert=999 → 残余 parts=1）。

### N7【升级为高风险 → ✅ 已修复】排队期取消的僵尸 task 会话
- 位置：`chat.py` `_begin_task_session`（`sem` 之前创建子会话并登记 task_bridge），`async with sem` 原在 `try` **之外**。
- 风险：排队期间取消 → `CancelledError` 在 `sem.acquire()` 挂起点抛出，**不经过**取消处理分支 → 子会话残留 `idle` 僵尸、task_bridge 映射残留、`_queue_counter` 未递减（计数失真 +1）。
- 修复：将整段（排队判断 + `async with sem` + 执行体）移入外层 `try`，新增外层 `except asyncio.CancelledError`：归还排队计数、子会话置 `interrupted`、`unregister`，再 `raise`。

### N2【确认存在 — 低概率 → ✅ 已修复】权限请求重复审批
- 位置：`permission/manager.py` `create_request` 无 `(path, operation)` 去重。
- 风险：同一轮多个并发工具对同一路径触发 `NeedsPermission` 时弹出重复审批（触发需 LLM 同轮并行写同一新路径，概率低）。
- 修复：新增 `_pending_by_key: {(path, operation) → request_id}`，pending 状态复用；`respond` / `await_decision` 超时 / `cleanup_expired` 三处进入终态后移除索引。已用内存实例单测验证复用/释放语义。

### N3【降级为低 → ✅ 已修复】强制收尾残留 tool_calls
- 位置：`graph.py` 强制收尾调用传 `tools=None`，返回只取 `msg.content`，残留 tool_calls 不进 answer，仅可能残留 step/tool_start 事件。
- 修复：收尾后若 `msg.tool_calls` 非空，`logger.warning` 告警记录（便于排查模型违反禁用工具约束）。

### N6【核实为低】`remove_session` 递归删除
- 证据：db.py `PRAGMA foreign_keys=ON` + 表 `ON DELETE CASCADE`；`remove_session` 递归删除幂等无害。
- 结论：无需修改，仅确认不重复遍历（当前 BFS 栈实现已避免）。

### 既有修复复核（校验通过）
- 排队计数对称增减（chat.py）、WAL + busy_timeout=10000 + 连接池（db.py:196-207）、`_push_event` try/except 兜底（graph.py）。均验证通过。

### 冒烟验证（第三轮）
- 以 `.venv\Scripts\python.exe -m uvicorn main:app --port 8000` 实际启动：`/health` 返回 200 `{"status":"ok","vector_store_size":0}`。`/api/monitor/stats` 401 系 auth 中间件要求 `X-User-Id`（预期，非缺陷）。验证后已停止进程。
- **后端全量编译**：`python -m compileall -q app main.py scripts` ✅ 无语法错误。
- **前端构建**：`frontend` 下 `npm run build`（vue-tsc + vite）✅ 构建成功，无 TS 类型错误。
- **N7 排队取消回归**（实际驱动 `/multi-agent/stream` 端点，容量 1）：请求 1 持槽 → 请求 2 收到 `{"type":"queued","queue_position":1}` → 取消请求 2 → 断言 `_queue_counter` 归零、子会话置 `interrupted`、`task_bridge` 已 `unregister`、请求 1 不受影响（保持登记、正常结束）✅。

---

## 七、修复记录（第三轮新增）

| 文件 | 修复 |
|---|---|
| `backend/app/api/chat.py` | N7：排队期取消的清理（计数归还 + 子会话 interrupted + unregister + re-raise） |
| `backend/app/session/repository.py` | N5：`IN (...)` 按 `_SQLITE_MAX_VARS=500` 分批（select / delete 各一处） |
| `backend/app/permission/manager.py` | N2：`(path, operation)` pending 审批去重 + 终态索引清理 |
| `backend/app/agent/graph.py` | N3：强制收尾残留 tool_calls 告警 |

全部通过 `py_compile`；N2/N5 单测通过；后端启动冒烟通过。

---

## 八、建议的下一步

1. 端口 8000 已确认空闲，后端冒烟已通过（本轮实际启动验证）；全量 `compileall`、前端 `npm run build`、N7 排队取消回归均通过。
2. 需要更长记忆时，在 `.env` 设置 `MEMORY_TTL_SECONDS`（如 `86400`）。
3. 可选：把记忆工具与子 Agent 的共享语义写进 AGENTS.md（现已与实现一致：`tool_task` 子 Agent 与主 Agent 同一 `conversation_id` namespace，可互相检索）。
4. 前端 `stores/multiAgent.ts` 事件消费交叉验证已在前两轮完成（与后端事件字段匹配），无需进一步改动。
5. 本轮验证结论（原"环境不支持代跑编译/构建"）已证伪：本环境实际可执行 `compileall`、`npm run build` 与 N7 回归，均通过。
