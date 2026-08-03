# AI 助手 Session 管理设计

> 参考 opencode 的 session 管理设计思路，适配本项目的 **FastAPI + SQLite + LangGraph + AgentBus** 技术栈。
>
> opencode 核心设计（源码参考）：
> - 数据模型：`packages/core/src/session/sql.ts`（session/message/part 归一化表，`project_id` 外键隔离）
> - 项目/位置隔离：`packages/core/src/location.ts`、`location-services.ts`（每 Location 一套独立服务图）
> - 事件溯源 + 投影：`session_message` 追加日志 + `SessionProjector`
> - 上下文纪元：`context-epoch.ts`（per-session 系统上下文快照 + baseline seq）
> - 执行协调器：`run-coordinator.ts`（per-session 串行，跨 session 并行）
> - 输入队列：`input.ts`（steer/queue 两种投递，admit → promote → wake）
> - 子会话：`session.ts`（`parent_id` 外键，级联删除，fork 克隆）

---

## 1. 背景与现状

当前 `AgentSuper` 的会话实现（`backend/app/api/chat.py`）：

| 现状 | 问题 |
| --- | --- |
| `conversations` 单表，整段 `messages` 存 JSON blob | 消息无法独立增删/追溯，体积随会话膨胀 |
| 无项目/工作区维度，仅按 `user_id` 隔离 | 无法做"按项目隔离会话" |
| 无子会话概念 | subagent/后台任务与主会话混在一起 |
| 全局 `asyncio.Semaphore(2)` 并发控制 | 同一会话内并发写消息会互相覆盖；不同会话也不得不停顿 |
| 滑动窗口截断 + 可选分层摘要 | 无持久化的"压缩基线"，恢复后无法定位截断水位 |

目标：按 opencode 的设计原则重构，保留现有对外 API（`/api/chat/*`）兼容。

---

## 2. 设计目标与原则

1. **以会话为中心的数据模型**：session / message / part 归一化表，支持子会话树。
2. **三级隔离**：`User → Project/Workspace → Session`，Session 内部自我隔离（历史/上下文/并发）。
3. **事件溯源式消息日志**：`session_messages` 追加写（append-only），投影出视图，天然支持撤销/重放/断点续传。
4. **上下文纪元**：每个 session 持久化自己的系统上下文快照与压缩基线，恢复/压缩后可精确定位历史水位。
5. **per-session 串行执行协调器**：同一 session 的输入串行执行、跨 session 并行，并受全局并发上限约束。
6. **输入投递模型**：显式区分"立即打断"（steer）与"排队"（queue）。
7. **可迁移、可增量上线**：新库 + 一次性数据迁移，旧表保留只读。

---

## 3. 总体架构

```
                    ┌──────────────────────────────────────────┐
   HTTP/SSE         │            FastAPI (app/main.py)         │
   ────────────────►│  deps.resolve_session_context()          │   ← 隔离中间件
                    │        │         │         │             │
                    │        ▼         ▼         ▼             │
                    │  User   │ Project │ Session │ 子会话树    │
                    ├──────────────────────────────────────────┤
                    │         SessionService (app/session)      │
                    │  ┌────────────┬──────────────┬──────────┐ │
                    │  │Repository   │ Coordinator  │ History  │ │
                    │  │(SQLite DML) │(per-session  │+Context  │ │
                    │  │             │ 串行/唤醒/打断)│ Epoch   │ │
                    │  └────────────┴──────────────┴──────────┘ │
                    │         │              │                  │
                    │         ▼              ▼                  │
                    │  session.db  ──►  inputs(队列) ──► wake   │
                    └─────────┬────────────────────────────────┘
                              ▼
                    ┌──────────────────────────────┐
                    │  Agent 执行层                 │
                    │  RAGAgent / AgentBus / LangGraph│
                    └──────────────────────────────┘
```

- **数据层**：新 SQLite 库 `backend/data/session.db`（区别于旧 `conversations.db`）。
- **服务层**：`app/session/` 模块，纯业务，不直接依赖 FastAPI。
- **隔离层**：`deps.py` 解析 `X-User-Id` + 项目 + session，做归属校验后注入 `SessionContext`。
- **执行层**：`coordinator.py` 调度 Agent 调用，返回事件流（复用现有 SSE 协议）。

---

## 4. 隔离模型

### 4.1 三级隔离维度

```
User (X-User-Id, 默认 anonymous)
 └── Project (工作区根目录的稳定哈希 id，git root 探测)
      └── Workspace (可选，控制面，如不同知识库/分支)
           └── Session (会话，核心隔离单元)
                ├── child Session (subagent / 后台任务 / fork)
                └── child Session
```

- **User**：会话列表、归属校验的最小边界。`sessions.user_id` 上建索引。
- **Project**：`project_id = sha1(项目根绝对路径)`。项目根 = 最近 git 仓库根（有 `.git`）否则回退到配置的默认工作区。同项目下的会话共享知识库（ChromaDB）上下文。
- **Workspace**：预留维度，对齐 opencode 的 `workspace_id`（如桌面/CLI/不同沙箱）。当前版本可空。
- **Session**：唯一运行上下文，自带消息日志、上下文纪元、执行队列、token/成本统计。

### 4.2 会话树（子会话）

- 子会话用 `sessions.parent_id` 表示，`kind` 区分用途（`task` / `subagent`）。
- 特性（对齐 opencode）：
  - **级联删除**：删除父会话递归删除全部子会话并取消其后台任务（对应 `session.ts:remove`）。
  - **fork**：在某消息处克隆出一个新根会话，重建 `parentID` 映射，独立演进（对应 `session.ts:fork`）。
  - **隔离上下文**：子会话拥有独立消息日志与上下文纪元，与父会话互不污染。

---

## 5. 数据模型

SQLite 库：`backend/data/session.db`。DDL 见 `backend/app/session/db.py`。

### 5.1 sessions（会话主表）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PK | `uuid4`，兼容前端 `conversation_id` |
| `slug` | TEXT | 短可读 ID |
| `version` | TEXT | 兼容版本号 |
| `user_id` | TEXT | 隔离维度 1（索引） |
| `project_id` | TEXT FK→projects | 隔离维度 2（索引，级联删除） |
| `workspace_id` | TEXT NULL FK→workspaces | 隔离维度 2.5（索引） |
| `parent_id` | TEXT NULL FK→sessions | 子会话（索引） |
| `directory` / `path` | TEXT | 会话工作目录 / 相对项目根路径 |
| `title` | TEXT | 标题 |
| `agent` / `model` | TEXT | 使用的 agent 与模型（model 为 json） |
| `kind` | TEXT | `chat` / `multi-agent` / `task` |
| `status` | TEXT | `idle/queued/running/retry/interrupted/error` |
| `cost` / `tokens_*` | REAL / INT | 会话累计统计（对齐 opencode tokens/cost） |
| `time_created/updated/compacted/archived` | INT | 时间戳 |

### 5.2 session_messages（事件日志，append-only）

- `(session_id, seq)` 复合主键，`seq` 为会话内递增序列（对齐 `session_message` 的 seq）。
- `type`：`user` / `assistant` / `system` / `compaction` / `epoch` / `tool`。
- 对应 opencode 的 `SessionHistory.load`：读取 `seq >= 压缩基线` 且 `seq > 上下文纪元 baseline_seq`（跳过系统消息）后的消息。

### 5.3 message_parts（部件）

- `text` / `reasoning` / `tool` / `file` / `patch` / `step`，按 `message_id` 归属。
- 对应 opencode 的 `PartTable`（`part_message_id_id_idx`）。

### 5.4 session_context_epoch（上下文纪元）

- `session_id` PK，`baseline`（系统提示 baseline）、`baseline_seq`（消息水位）、`snapshot`（系统上下文快照 json）。
- 对应 opencode `context-epoch.ts`：运行前 `initialize/prepare` 自己的 epoch，`replace` 在压缩后重建水位。

### 5.5 session_inputs（输入队列）

- `(session_id, id)` PK；`delivery ∈ {steer, queue}`；`admitted_seq`（入队水位）→ `promoted_seq`（提升水位）。
- 对应 opencode `input.ts`：`admit → promote → execution.wake(session_id)`。
  - `steer`：打断当前回合，立即并入下一次 provider turn。
  - `queue`：排到当前回合之后执行。

### 5.6 session_tasks（任务状态，桥接现有 `tasks.db`）

- 保留现有 `tasks` 表能力，新增 `session_id` 外键，支持子任务 `parent_task_id`。

### 5.7 projects / workspaces

- `projects(id, name, root, vcs)`：`id = sha1(root)`，`root` 为项目根绝对路径，`vcs` 为 `git`/`''`。
- `workspaces(id, project_id, name)`：预留。

---

## 6. 消息与上下文管理

### 6.1 历史读取（对齐 opencode `SessionHistory.load`）

```
SessionHistory.load(db, session_id):
  epoch      = SELECT baseline_seq FROM session_context_epoch WHERE session_id=?
  compaction = SELECT MAX(seq) FROM session_messages WHERE session_id=? AND type='compaction'
  rows       = SELECT * FROM session_messages
               WHERE session_id=?
                 AND seq >= compaction.seq
                 AND (seq > epoch.baseline_seq OR type != 'system')
               ORDER BY seq
```

### 6.2 上下文纪元（对齐 opencode `context-epoch.ts`）

- 每个 session 首次运行前 `initialize`：生成系统上下文快照（知识库配置 + 检索策略 + 技能/插件清单），写 `baseline_seq = 当前最新 seq`。
- 后续运行 `prepare`：与快照做 `reconcile`；若快照已过期且存在压缩则 `replace`（重建 baseline）。
- 好处：**恢复/重放时只发送 epoch 之后的消息**，不再依赖"截断数组"推断水位。

### 6.3 压缩（对齐 opencode `compaction` + 现有 `summarization.py`）

- 触发条件：单轮 usage 超阈值或会话累计 token 超阈值（现有 `MAX_HISTORY_TOKENS`）。
- 流程：写一条 `type='compaction'` 消息（内容为结构化 checkpoint，可复用现有 `COMPACTION_PROMPT`），随后 `replace` 上下文纪元 baseline。
- 恢复时：读取从最新 compaction 起的消息 + checkpoint，避免重放整段历史。

---

## 7. 执行模型

### 7.1 per-session 串行协调器（对齐 opencode `run-coordinator.ts`）

```python
class SessionCoordinator:
    """同一 session 的输入串行执行；不同 session 并行；受全局 Semaphore 约束。"""
    # run(session_id): 幂等 join 正在运行的同一 session 任务
    # wake(session_id): 有新增输入时注册唤醒，合并多次唤醒
    # interrupt(session_id): 打断当前 fiber，置 status=interrupted
    # active: 当前正在执行的 session 集合
```

- 替换现有 `_agent_semaphore` 的"全局排队"，改为 **key = session_id** 的协调器 + 全局并发上限（保留 `MAX_CONCURRENT_AGENTS`）。
- 队列位置信息继续通过现有 SSE `queued` 事件透出（前端已支持）。

### 7.2 输入投递（对齐 opencode `input.ts`）

- `POST /api/sessions/{id}/prompt` → `admit(prompt, delivery)` → `execution.wake(session_id)`。
- 兼容现有 `/api/chat/stream`：内部仍是一次 `admit(steer)` + `wake` + SSE 流。

### 7.3 工具执行与撤销

- 每个 tool 调用落 `message_parts(type='tool')`（pending → running → completed/error），支持恢复后标记中断工具（对应 `failInterruptedTools`）。
- 会话级 undo：基于 `snapshot` 或部分删除 `message_parts`（对应 opencode `revert.ts`，二期）。

---

## 8. 服务与 API

### 8.1 SessionService 接口（`app/session/service.py`）

```python
create(user_id, project_id, parent_id=None, agent=None, model=None, kind='chat')
get(user_id, session_id) / list(user_id, project_id=None, search=None, roots_only=False)
fork(user_id, session_id, message_id=None)
update(user_id, session_id, title=None, archived=None, agent=None, model=None)
remove(user_id, session_id)            # 级联删除子会话 + 取消后台任务
append_message(user_id, session_id, msg) / append_part(...)
context(user_id, session_id)           # 模型视角历史（epoch 之后）
admit_input(user_id, session_id, prompt, delivery) / promote(...)
children(user_id, parent_id) / status(user_id, session_id)
```

### 8.2 REST 路由（`app/session/router.py`，前缀 `/api/sessions`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/sessions` | 创建会话（body: `project?/parent_id?/agent?/model?/kind?`） |
| GET | `/api/sessions` | 列表（query: `project?/workspace?/search?/roots?/archived?`） |
| GET | `/api/sessions/{id}` | 详情 |
| PATCH | `/api/sessions/{id}` | 改标题/归档/agent/model |
| DELETE | `/api/sessions/{id}` | 删除（级联） |
| POST | `/api/sessions/{id}/fork` | fork 到子会话 |
| POST | `/api/sessions/{id}/prompt` | 投递输入（`delivery: steer|queue`） |
| GET | `/api/sessions/{id}/messages` | 分页消息 |
| GET | `/api/sessions/{id}/context` | 模型视角上下文 |
| POST | `/api/sessions/{id}/compact` | 手动压缩 |
| POST | `/api/sessions/{id}/revert` | 撤销到指定消息（`message_id`） |
| POST | `/api/sessions/{id}/interrupt` | 打断 |
| GET | `/api/sessions/{id}/children` | 子会话列表 |
| GET | `/api/sessions/{id}/status` | 状态（active/queue） |

### 8.3 隔离依赖（`app/session/deps.py`）

```python
async def resolve_session_context(request, session_id) -> SessionContext:
    user_id  = header X-User-Id or "anonymous"
    session  = repository.get(session_id)
    if session.user_id != user_id: 403
    project  = projects[session.project_id]
    return SessionContext(user=..., project=..., session=..., repo=..., service=...)
```

> 对应 opencode `session-location.ts` 中间件：所有 session 操作都解析到该 session 自己的"位置上下文"。

---

## 9. 模块骨架

```
backend/app/session/
├── __init__.py       # 导出 SessionService / SessionContext
├── db.py             # SQLite 连接 + 建表 DDL + 迁移
├── models.py         # Pydantic 模型（SessionInfo/Message/Part/Input/Ctx）
├── repository.py     # 数据访问（DML + 查询 + 级联删除）
├── coordinator.py    # per-session 串行协调器
├── history.py        # 历史/上下文纪元装载（对齐 SessionHistory.load）
├── service.py        # 业务服务（组合 repository+coordinator+history）
├── deps.py           # FastAPI 隔离依赖
└── router.py         # /api/sessions 路由
```

---

## 10. 迁移与兼容

1. **新库并存**：新建 `session.db`，旧 `conversations.db` 保持只读。
2. **一次性迁移**：读旧 `conversations` 表 → 每行生成 `sessions` + `session_messages`（按 role 映射 user/assistant，`seq` 递增）；`user_id` 沿用。
3. **API 兼容**：`/api/chat/*` 保留薄壳，内部调用新 SessionService；响应继续带 `conversation_id`。
4. **前端兼容**：前端 `conversation_id` 即 `session.id`，无需改动。

---

## 11. 实施路线

| 阶段 | 内容 | 依赖 | 状态 |
| --- | --- | --- | --- |
| P0 | `db.py` + `models.py` + `repository.py`（建表/CRUD/迁移） | 无 | ✅ 完成 |
| P1 | `history.py` + `context-epoch`（历史装载 + 上下文纪元） | P0 | ✅ 完成 |
| P2 | `coordinator.py` + `service.py` + `deps.py` + `router.py` | P0/P1 | ✅ 完成 |
| P3 | 接入 `chat.py`：`/api/chat/*` 改为走 SessionService | P2 | ✅ 完成 |
| P4 | 子会话/任务：`AgentBus` 任务登记为子会话，支持 fork/级联取消 | P2 | ✅ 完成 |
| P5 | 压缩基线持久化 + undo/revert（`revert.ts` 思路） | P1 | ✅ 完成 |

> P3 落地细节：新增 `agent_executor.py`（coordinator drain → RAGAgent，SSE 事件桥经
> `request_id` 回填请求级队列）；`main.py` lifespan 建 `session.db` + 注入 `SessionService`；
> `/stream` 改走 SessionService；会话 CRUD 读 session.db（旧 conversations.db 惰性迁移）；
> `stream_status` 改为反映协调器并发。
>
> P4 落地细节：新增 `task_bridge.py`（child_session_id ↔ thread_id 映射，`cancel`/`cancel_children`
> 对齐 opencode abort）；`bus.py` 增 `cancel_pending(thread_id)`；`service.remove`/`interrupt`
> 级联子会话（coordinator + task_bridge 双通道）；`fork` 按 `message_id` 用 `_copy_message` 复制
> 消息（含 parts）；`chat.py` 的 `/multi-agent` 与 `/multi-agent/stream` 登记 `kind='task'` 子会话
> + thread 并回写父/子消息。验证：TestClient/httpx 端到端 5 组场景（子会话生成、流式、fork、
> 级联取消、interrupt）全部通过。
>
> P5 落地细节：压缩基线持久化——executor 在压缩实际发生时落 `type='compaction'` 消息 +
> `replace_epoch_after_compaction` + 置 `session.time_compacted`；`history.load` 改为把最新
> compaction 消息作为 system 上下文带回（对齐设计 §6.1），恢复/重放不再丢摘要；首条消息标题
> 判定改用 `latest_seq==0`（避免被 compaction 消息干扰）。undo/revert——`repository.revert_to_message`
> 删除目标消息之后的所有消息与 parts，并按剩余最新 compaction 回滚纪元水位；`service.revert` +
> `POST /api/sessions/{id}/revert`；`compact` 补写 `time_compacted`。验证：e2e 覆盖压缩基线、
> revert 回滚（含越权 403）、executor 自动压缩、标题生成、HTTP /revert+/compact，全部通过。
