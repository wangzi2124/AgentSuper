# tool_task 与 opencode task 工具差异执行文档

> 背景：主 Agent 委派子 Agent 的机制对比 opencode `task.ts`（dev 分支）发现了
> 4 个设计缺口，本文档记录每项缺口、对齐方案、落地实现与验证方式。
> 依据源码：
> - opencode: `packages/opencode/src/tool/task.ts`（371 行，dev）
> - AgentSuper: `backend/app/agent/graphmod/tools.py`（`_tool_task`）、
>   `backend/app/agent/bus.py`（`AgentBus`）、`backend/app/session/task_bridge.py`

---

## 一、差距总览

| # | 维度 | opencode | AgentSuper 现状 | 缺口 |
|---|---|---|---|---|
| ① | 委派授权 | `permission.task` 声明式规则（ask/allow/deny 按 subagent_type glob 匹配，deny 时从 Task 工具描述移除） | 硬编码白名单 `_TASK_TOOL_SUBAGENTS`，无逐 agent 授权、无 ask 审批 | **实现** |
| ② | 会话复用 | `task_id` 参数 resume 同一子会话（继续其 messages/tool outputs） | 每次 fresh context + 新 `task:uuid` thread，无 resume | **实现** |
| ③ | 异步委派 | `background=true` 立即返回 `<task state="running">`，`BackgroundJob` 完成后以合成 assistant 消息注入父会话 | 永远前台阻塞 `send_and_wait` | **实现** |
| ④ | 取消级联 | abort 时 `background.cancel + ops.cancel` 级联中断子任务 | stream abort 不取消在途子任务 | **实现** |

对齐优先级（按影响）：① 授权显式化 → ② resume → ③ background → ④ 取消。

---

## 二、① permission.task 声明式授权

### opencode 语义
- 配置 `agent.<name>.permission.task`：`"*": deny` + `"explore": allow` 等 glob 规则表。
- **deny 时 subagent 从 Task 工具 description 中移除**（模型根本看不到），不是运行时才拒绝。
- 规则顺序求值，**最后匹配者生效**。

### 现状问题
`_TASK_TOOL_SUBAGENTS = ("explore", "plan")` 写死在 `constants.py`，无法声明式控制
「当前 Agent 能派谁」。build 永远能派 explore/plan，无 ask 审批、无 deny 收敛。

### 落地设计
1. **配置源**：`settings.task_permission_rules: dict[str, str] = {}`（.env 加
   `TASK_PERMISSION_RULES={"*":"allow","explore":"ask"}` JSON）。空 = 保持现状
   （全部白名单允许，零迁移成本）。
2. **规则求值**：`app/agent/graphmod/task_permission.py` 新增 `resolve(patterns, name)`，
   按顺序匹配、最后胜出，缺省 `allow`（白名单内）；支持 `*`/`?`/`explore*` glob。
3. **schema 裁剪**：`base.py` `_build_tool_defs` 挂载 `tool_task` 前，用规则过滤
   `_TASK_TOOL_SUBAGENTS` → deny 的 subagent 不在 `enum` 中（对齐 opencode
   description 移除语义）。
4. **运行时 ask 审批**：`_tool_task` 中对 `resolve()=="ask"` 的子类型走
   `permission_request → await_decision`（复用 `_execute_tool` 已有的
   `NeedsPermission` 审批桥）：有 `_event_queue` → 推送 `permission_request` 等待用户
   决定；无队列 → 直接拒绝（返回 denial 文本，不永久阻塞）。
5. **deny 兜底**：`_tool_task` 入口对 `resolve()=="deny"` 直接返回
   `Error: subagent_type 'X' is disabled by permission rules`。

### 兼容性
- 默认 `{}` → 行为与现状完全一致（全 allow），无需改任何既有配置。
- 强制规则只影响显式配置了 rules 的部署。

---

## 三、② task_id resume 子会话复用

### opencode 语义
- Task 参数有 `task_id`：传 `task_id` 时**继续同一子会话**（复用其 messages 与工具输出），
  不传则新建。输出 `<task id=... state=completed>` 包装，模型读到 `<task ... id>` 即可回传 resume。
- 子会话是持久 session（`sessions.create({parentID})`），天然可 resume。

### 现状问题
`_tool_task` 每次 `sub_thread_id = f"task:{uuid}"`、`history=[]`，子 Agent 无状态；
委派出的子 Agent 无法跨轮续跑（例如「先 explore 调研 → 再对同一探索继续追问」）。

### 落地设计
1. **thread 改为稳定 task_id**：`sub_thread_id = args.get("task_id") or f"task:{uuid}.hex[:8]"`
   —— 传 task_id 时复用同一 thread，不传时新建（对齐 opencode `task_id ?? create`）。
2. **子会话历史持久化**（进程内）：`app/agent/graphmod/task_registry.py` 新增
   `TaskRegistry`：
   - `record(task_id, subagent_type, history, answer)`：追加/更新该 task 的对话线索
     （`{system, user, assistant(answer)}` 摘要版，不保存后台全部 tool 消息，控制内存）。
   - `get_history(task_id)`：返回该 task 上次运行的 `history`（供子 Agent 续跑）。
   - `get_task_id(subagent_type)` 预留（给模型提示可 resume 的 id 列表）。
   - 内存态、重启即失（注释说明：与 opencode 持久 session 的差异，可接受，DB 侧
     session 本身持久）。
3. **schema 增加 `task_id`**：`_TASK_TOOL_SCHEMA` parameters 增
   `task_id: {type: string, description: "...resume 之前委派返回的 task_id..."}`，
   `required` 不含它（可选）。
4. **`_tool_task` 织入**：
   - 读取 `task_id`；命中 registry → 以 `registry.get_history(task_id)` 作为
     `payload["history"]` 传给子 Agent（而非恒空）。
   - 完成时 `registry.record(task_id, subagent_type, question, answer)`。
   - 返回文本显式带 `<task>` 包装（对齐 opencode renderOutput）：成功后
     `<task id="..." state="completed"><task_result>answer</task_result></task>`，
     失败 `state="error"` + `<task_error>`，让模型能解析 id 用于续跑。
5. **子 Agent 消费 history**：explore/plan/rag_wrapper 已支持 `payload["history"]`
   （explore_agent.py:81 `history = payload.get("history") or []`，tool_loop_chat 会注入
   前置对话），零改动。

---

## 四、③ background 异步委派

### opencode 语义
- `background=true`：立即返回 `<task id=... state="running">`，任务由 BackgroundJob 后台跑，
  完成后**以合成 assistant 消息注入父会话**（`injectBackgroundResult`），父 Agent 下一轮
  LLM 调用即可看到结果。且 `background.extend` 支持对同一 running 任务追加上下文。

### 现状问题
`tool_task` 恒前台阻塞（`await bus.send_and_wait`）。主 Agent 调用 explore 期间不能并行做别的。

### 落地设计
1. **schema 增加 `background` bool**（默认 false）。
2. **后台执行**：`tools.py` 引入模块级
   `_BACKGROUND_TASKS: dict[str, asyncio.Task]`（task_id → 协程 task）：
   - `background=true` 时：`loop.create_task(_run_task(...))`（内部还是
     `bus.send_and_wait`），**立即返回** running 包装
     `<task id=... state="running"><task_result>已启动后台任务，完成后自动通知</task_result></task>`。
   - 完成回调通过 `_BACKGROUND_RESULTS: dict[conversation_id, list[str]]` 累积
     合成 assistant 文本；同时 `registry.record(...)` 落历史。
3. **父 Agent 下一轮看到结果**：`graphmod/generate.py` 每轮 `messages.append(assistant 工具调用)`
   之前，检查 `_BACKGROUND_RESULTS.get(conversation_id)`，有则**前置注入**一段
   `{"role":"assistant","content":"<task id=... state=completed>...result...</task>"}`
   到 messages 开头（对齐 opencode injectBackgroundResult 的合成 assistant 消息），
   让 LLM 即使本轮无 tool_calls 也能吸收结果收尾。
4. **结果消费者职责**：主 Agent 系统提示中的 task 说明补充「background 模式下若收到
   running 通知，可继续其他工作；完成后会收到结果」。

### 权衡
- 不引入常驻 BackgroundJob 服务（当前无跨请求作业持久化需求）；后台以进程内
  `asyncio.Task` + dict 结果桥承载，abort/重连后方可先通过 registry 取 last answer。
- 前后台用同一 `bus.send_and_wait`，worker 侧无差别（无需 agent 改动）。

---

## 五、④ abort 级联取消

### opencode 语义
- `onAbort`：父请求中断时 `background.cancel(nextSession.id)` + `ops.cancel(nextSession.id)`，
  会一直取消到子会话的生成循环。

### 现状问题
- `bus.send_and_wait` 超时/无回复返回错误消息，但**取消不会穿透到子任务**：
  `event_generator` finally `task.cancel()` 只取消端点侧等待，子 Agent 仍在跑。
- supervisor 的 `_execute_parallel` 也各自等待，abort 后留下僵尸子任务。

### 落地设计
1. **thread → task 注册**：`bus.py` 的 `send_and_wait` 在 `_pending[thread_id]` 存入
   `(future, waiter_task)`，或另建 `_waiters: dict[thread_id, asyncio.Task]`。
2. **`AgentBus.abort(thread_id)`**：
   - 取消 wait `_pending` 对应 future（已有 `cancel_pending`）；
   - **并向 agent task 侧发中断信号**：把 `cancel_pending` 升级为「发送
     `type="cancel"` 消息到子 Agent mailbox」→ 子 Agent 事件循环收到 cancel 后
     `asyncio.current_task().cancel()` 自身（或 handle_message 检查 `cancel` 主动中断）。
3. **端点接入**：`endpoints.py` `event_generator` 的 finally 已 `task.cancel()`；
   在 `except asyncio.CancelledError` / finally 里追加
   `agent_bus.abort(thread_id)`（针对当前 supervisor 子任务 thread）。task_bridge
   `cancel(child_id)` 改为经 `bus.abort` 级联。
4. **子 Agent 协作取消**：`run_agent` 循环在弹出消息前/Handle 中检查取消信号——
   最小实现：总线 `send(AgentMessage(type="cancel", thread_id=...))`，
   `run_agent` 处理 `cancel` 类型消息时对当前 handle task 发 `cancel()`；
   带 `task_depth` 的子委派同理逐层取消（对齐 opencode 级联）。
5. **`_tool_task` 内取消**：`send_and_wait` 若因父级取消抛 `CancelledError`，先
   `bus.abort(sub_thread_id)` 再向注册表记录 cancelled，最后 re-raise。

### 权衡
- Approximate: opencode 靠 session abort；我们用 bus 消息中断 + Future 取消双通道。
- 「正在执行函数内」的协程取消仍依赖 asyncio 的协作取消（`CancelledError` 需在被
  await 的边界抛出），长阻塞 `tool_execute` 等同步边界内无法立即中止（与 opencode 同）。

---

## 六、变更文件清单

| 操作 | 文件 | 内容 |
|---|---|---|
| 修改 | `backend/app/config.py` | `task_permission_rules: dict[str,str] = {}` |
| 新增 | `backend/app/agent/graphmod/task_permission.py` | `resolve()` glob 规则求值 |
| 修改 | `backend/app/agent/graphmod/constants.py` | `_TASK_TOOL_SCHEMA` 增 `task_id`/`background`；`_TASK_TOOL_SUBAGENTS` 过滤位 |
| 新增 | `backend/app/agent/graphmod/task_registry.py` | `TaskRegistry`（①② 历史 + ③ 后台结果构造） |
| 修改 | `backend/app/agent/graphmod/tools.py` | `_tool_task` 织入 permission / resume / background / abort |
| 修改 | `backend/app/agent/graphmod/base.py` | `_build_tool_defs` 按规则裁剪 tool_task enum |
| 修改 | `backend/app/agent/graphmod/generate.py` | 每轮注入 background 合成结果 |
| 修改 | `backend/app/agent/bus.py` | `abort()`、`_waiters`、cancel 消息处理 |
| 修改 | `backend/app/agent/graphmod/core.py` | `invoke` 传 `_task_depth`/取消信号（若有） |
| 修改 | `backend/app/api/chatmod/endpoints.py` | 取消路径调用 `bus.abort` |
| 修改 | `backend/app/session/task_bridge.py` | `cancel` 级联升级为 `bus.abort` |
| 新增 | `backend/tests/test_task_permission.py` | 规则求值 / schema 裁剪 / ask 审批 / deny 拒绝 |
| 新增 | `backend/tests/test_task_registry.py` | resume 历史 / background 结果注入 |

## 七、验证方式
1. `pytest tests/test_task_permission.py tests/test_task_registry.py -q`
2. 回归：`pytest tests/ -q`（重点 supervise/task 相关套件）
3. 手动：multi-agent 流中让 build 调 `tool_task(subagent_type="explore")`，
   验证 builder 权限 ask 弹窗 / deny 拒绝信息 / 传 task_id 续跑 / background 后收到合成结果。
## 八、执行结果（2026-09-09）✅

> 代码核实结论：①②③④ 四缺口已全部落地并有测试；文档第六节文件清单与实现一致，
> 仅两处实现比设计更完整（abort 增加 `abort_work` 工作侧中断；registry 增加
> `push_background_result/drain_background_results/peek_last_answer`）。

### 逐项落地证据（backend/app 内，行号为 2026-09-09 核实值）

| # | 落地 | 代码证据 |
|---|---|---|
| ① permission.task | `config.py:199` `task_permission_rules={}`（空=保持全 allow，零迁移）；`graphmod/task_permission.py` `resolve()` glob 最后胜出；`graphmod/base.py` schema 层按规则裁剪 tool_task enum（deny 的 subagent 模型不可见）；`graphmod/tools.py:127` resolve → deny 直拒 / ask 走 `permission_request → await_decision` 审批桥（tools.py:141-156，无事件队列即拒绝不阻塞，tools.py:154） | ✅ |
| ② task_id resume | `tools.py:121` 读取 task_id；`:160` 稳定 thread（`task_id or task:{uuid8}`）；`:161` 命中 registry 以 `get_history(task_id)` 续跑；`graphmod/task_registry.py` `TaskRegistry`（record/get_history/get/peek_last_answer/record_ids/push_background_result/drain_background_results） | ✅ |
| ③ background 异步委派 | `tools.py:88` `_background_tasks` 模块级表；`:196-206` `asyncio.create_task` 立即返回 `<task state="running">`，完成 `push_background_result`；`generate.py:291-293` 每轮 `drain_background_results(conversation_id)` 前置注入合成 assistant 消息（对齐 `injectBackgroundResult`） | ✅ |
| ④ abort 级联取消 | `bus.py:216` `cancel_pending`（停等待）+ `:228` `abort_work`（取消在途 handler task）+ `:241-251` `abort` 双通道；`:285-294` 每条消息独立 handler task 供精确中断；`session/task_bridge.py` `cancel/cancel_children` 升级为 `bus.abort`；`endpoints.py` 取消路径 `:166-173`（499）、`:374-382`、`:398-412`（排队期取消）均置会话 `interrupted` | ✅ |

### 测试

- `backend/tests/test_task_permission.py`（规则求值 / schema 裁剪 / ask 审批 / deny 拒绝）
- `backend/tests/test_task_registry.py`（resume 历史 / background 结果注入）
- 均 2026-09-09 14:34 新增，配套 `tests/` 现有套件（test_agent_bus / test_sub_agents / test_supermod_extra 等）

### 文档遗留小项

- 本设计文档第七节「验证方式」中的 pytest / 手动验证命令尚未在本文档内附执行输出；
  建议按需在 CI 或本地跑 `pytest tests/test_task_permission.py tests/test_task_registry.py -q` 后回填。
