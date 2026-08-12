# AgentSuper 后端升级文档：会话三层架构（P1）+ Token 预算修复（P8）

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 适用代码基线 | E:\AgentSuper\backend（2026-08-12 前后） |
| 升级内容 | ① session 模块按 opencode `run-coordinator.ts` 对齐为 SessionService + Coordinator + Repository 三层；② P8 token 预算修复补丁（`token_patch/fix_token_budget_p8.py`） |
| 关联文档 | `token_patch/TOKEN_OPTIMIZATION_REPORT.md`、`token_patch/PATCH6_CHANGELOG.md` |
| 执行人 | 后端维护者 |

> 本文档面向"把当前工作区代码升级到目标形态"的操作手册。目标形态 = 三层会话架构（已落地）+ P8 token 预算修复（补丁脚本已就绪，**尚未验证应用状态**）。

---

## 一、升级背景与目标

### 1.1 为什么要升级

旧版会话链路存在三类问题：

1. **调度无协调器**：消息直接入队，缺少 per-session 串行 + 全局并发上限，`pendingWake` 唤醒存在丢失竞态，输入可能"入队但不被调度"。
2. **占位执行器丢数据**：`_default_executor` 占位会 promote 输入但不消费，存在数据丢失风险。
3. **Token 预算低估**：cl100k_base 对 DeepSeek tokenizer 系统性低估约 13.2%（实测 round8 估算 20,851 vs 实际 23,599；round9 实际 25,779 超 usable 23,808），压缩触发过晚（0.8 比例实测 round 8 才触发），max-steps 收尾路径只有截断、缺"清理→压缩→截断"闭环。

### 1.2 升级目标

- **P1 会话三层架构**：对齐 opencode `run-coordinator.ts`，SessionService（业务门面）→ Coordinator（调度/唤醒/中断）→ Repository（存储），executor 只负责执行与流式回填。
- **P8 Token 预算**：估算校正系数 1.13 落地；压缩触发参数化（`compaction_threshold_ratio`）；收尾路径补齐压缩闭环；单请求上下文天花板收敛（usable ≈ 15.8K 目标）。

---

## 二、变更清单

### 2.1 新增/重构模块：`app/session/`

| 文件 | 职责 | 对齐 opencode 参照 |
|---|---|---|
| `models.py` | `Message` / `Part` / `SessionInfo` / `ContextEpoch` / `InputRecord` 数据模型、`PartType` 枚举 | types/session |
| `repository.py` | SQLite 存储层：会话 CRUD（递归删除）、append-only 消息日志（单条 INSERT 原子计算 seq）、parts 批量加载/就地更新、上下文纪元（baseline_seq + snapshot）、输入队列（admit/promote/has_pending/count_pending/clear）；WAL + write_lock 保证并发写安全 | session repository |
| `coordinator.py` | `SessionCoordinator`：per-session 串行 + 全局并发上限信号量、wake（pendingWake 合并）、interrupt（cancel + stopping）、cancel_best_effort | run-coordinator.ts |
| `service.py` | 业务门面：write_lock 串行化消息追加、`prompt()` 落库 + 投递（经 coordinator 调度/唤醒）、compact/revert/title 生成 | SessionService |
| `agent_executor.py` | 单/多 Agent 双分支执行器、请求级事件队列桥、`PartBridgeQueue` 流式部件回填、`classify_error` | run-executor |
| `deps.py` | FastAPI 依赖：请求作用域 `SessionService` 实例（`get_session_service`） | — |
| `task_bridge.py` | 子任务执行桥（多 Agent 专用），register/unregister | — |
| `db.py` / `history.py` / `ids.py` / `router.py` | 建表/迁移、历史加载、ID 生成、session 路由（沿用/微调） | — |

### 2.2 修改的接口与入口

| 文件 | 变更点 |
|---|---|
| `app/api/chat.py` | `/stream` 改为「入队 → SSE 事件队列 → 事件流推送」，请求头尽早透出 `X-Session-Id`；`/stream/status` 使用 `coordinator.active_sessions + count_pending` 新语义；`/interrupt`、`/revert`、`/compact`、消息/部件 API 基于新 repository 层 |
| `app/context/budget.py` | 压缩阈值由硬编码 `usable * 0.8` 改为读取配置 `compaction_threshold_ratio`（**由 P8 补丁修改**） |
| `app/agent/graph.py` | max-steps 收尾路径补齐 `prune_tool_outputs → should_compact/compact → truncate` 闭环（**由 P8 补丁修改**） |

### 2.3 P8 补丁脚本内容（`token_patch/fix_token_budget_p8.py`）

- 支持 `--apply`（默认）/ `--verify` / `--rollback`；备份文件后缀 `.bak_p8`。
- 补丁点（共 2 处，脚本内 `PATCHES` 列表）：
  1. `app/context/budget.py`：`compaction_threshold_tokens` 计算从硬编码 `0.8` 改为
     `ratio = getattr(settings, "compaction_threshold_ratio", 0.65)` → `usable * ratio`。
  2. `app/agent/graph.py`：收尾（MAX_STEPS）路径在截断前补
     `prune_tool_outputs`（protect=24K / minimum=12K / tail_turns=2）与
     `compactor.should_compact/compact`，并打 `trace_messages` 事件。
- 注意：补丁的"默认值 0.65"是脚本兜底；实际生效值来自 `config.py` 的
  `compaction_threshold_ratio`（当前 0.6，见 2.4）。

### 2.4 配置项变化（`app/config.py` 现状核对）

| 配置项 | 当前代码值 | 说明 / 注意 |
|---|---|---|
| `token_estimate_correction` | `1.13` | P8 已落地：cl100k_base 低估校正（实测 +13.2%） |
| `compaction_threshold_ratio` | `0.6` | v9 注释：0.65→0.6（usable 降为 15.8K 后保持"压缩早于截断"窗口）。**与补丁脚本兜底默认值 0.65 口径不同，属预期**（脚本读取的是 config 值） |
| `compaction_threshold_tokens` | `0` | 0 = 自动按 `usable × ratio` |
| `context_tail_turns` | `2` | 对齐 opencode tail_turns |
| `context_preserve_recent_tokens` | `8_000` | 对齐 opencode preserve_recent_tokens |
| `tool_output_protect_tokens` | `24_000` | 回溯式工具输出清理阈值 |
| `tool_output_prune_minimum_tokens` | `12_000` | 清理收益下限 |
| `max_context_tokens` | `32_000` | ⚠️ **注释与代码不一致**：v9 注释写"32K→24K（usable≈15.8K）"，但代码仍是 `32_000`（usable=23,808）。**升级决策点，见 Step 3** |
| `context_reserve_tokens` | `8_192` | 输出预留 |
| `llm_max_tokens` | `8_192` | v9：16_384→8_192，压低超长输出兜底成本 |

---

## 三、升级前置检查

1. Python 版本与虚拟环境：`backend/.venv` 存在（`.python-version` 已配置），确认激活：
   ```bash
   cd E:\AgentSuper\backend
   .venv\Scripts\activate   # Windows
   python -V
   ```
2. 依赖完整性：`pip install -r requirements.txt`（或 `uv sync`）确认 `tiktoken`、`fastapi`、`uvicorn` 等已安装。
3. 检查 `backend/data/` 下现有 SQLite 库（如 `agentsuper.db`）是否存在旧表；**旧库升级路径见 Step 2**。
4. 确认 `token_patch/fix_token_budget_p8.py` 与 `token_patch/TOKEN_OPTIMIZATION_REPORT.md` 在仓库内（本机已有）。

---

## 四、升级步骤

### Step 1 备份

```bash
cd E:\AgentSuper\backend
# 1) 代码备份（建议 git，无 git 则整目录复制）
git add -A && git commit -m "pre-upgrade: session three-layer + P8 token budget"
# 2) 数据库备份
copy data\agentsuper.db data\agentsuper.db.bak_pre_session_p8
```

### Step 2 数据库初始化 / 迁移

- 新表结构（幂等建表）：`app/session/db.py` 的 `init_db()` 负责建表，包含
  `session_context_epoch`（baseline_seq + snapshot）、`session_inputs`（输入队列）、
  `message_parts`（部件流）及消息表 `seq` 列。
- **旧库升级检查清单**（启动前核对）：
  - [ ] `session_context_epoch` 表是否存在；不存在则建表并回填 baseline_seq = 该会话最小未压缩 seq；
  - [ ] `session_inputs` 表是否存在；
  - [ ] 消息表是否含 `seq` 列；旧数据若 `seq` 为 NULL，需按插入顺序回填为连续值；
  - [ ] `message_parts` 表是否就绪；
  - [ ] 确认 `init_db()` 在启动路径被调用（`app.main` 或 lifespan）。
- 迁移策略建议：**小库直接重建**（消息/部件导出后重放）；**大库走列补 + 回填脚本**，避免全量重写。
  （当前 `init_db()` 是否覆盖旧库升级属**未验证项**，见第七节。）

### Step 3 配置核对（含已知不一致）

在 `app/config.py` 二选一，**统一 v9 口径**：

- **方案 A（推荐，落地 v9 目标）**：`max_context_tokens = 24_000` → usable ≈ 15.8K，与注释、与 `MAX_HISTORY_TOKENS 16K` 配套一致；
- **方案 B（保守）**：维持 `32_000`，同时**修正注释**，避免误导（usable 实为 23,808）。

其余配置保持 2.4 表内当前值即可。核对 `.env` 未覆盖上述项（若覆盖，以 `.env` 为准）。

### Step 4 应用 P8 token 补丁

```bash
cd E:\AgentSuper\backend
python token_patch/fix_token_budget_p8.py --apply
python token_patch/fix_token_budget_p8.py --verify   # 期望：全部 [ok] 已应用
```

- `--verify` 失败排查：
  - `old 残留 > 0` 且 `new 存在=False` → 未应用，重跑 `--apply`；
  - `old 残留 = 0` 且 `new 存在=True` → 已应用（重复 apply 会自动跳过）；
  - 匹配数异常（文件已手改）→ 手工 diff `*.bak_p8` 后对齐。
- 应用后确认生成备份：`app/context/budget.py.bak_p8`、`app/agent/graph.py.bak_p8`。

### Step 5 启动服务

```bash
cd E:\AgentSuper\backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动日志应出现：建表/迁移提示、coordinator 初始化、无 import 报错。

---

## 五、验证清单

### 5.1 功能验证（session 三层架构）

| # | 用例 | 操作 | 预期结果 |
|---|---|---|---|
| F1 | 单 Agent 流式对话 | `POST /api/chat/stream`（普通问题） | SSE 事件顺序：`queued` → `message_start` → `text/part` 增量 → `done`；响应头含 `X-Session-Id` |
| F2 | DB 落库 seq | 会话结束后查消息表 | `seq` 从 1 连续递增，无空洞、无重复；`message_parts` 行与事件增量一致 |
| F3 | 流中即得会话 id | 首轮对话后立刻用 `X-Session-Id` 发起第二轮 | 历史正确衔接（前端已按约定注入 conversation_id） |
| F4 | 中断 | 长任务运行中 `POST /api/chat/{sid}/interrupt` | 后台任务收到 cancel/stopping，SSE 发 `error/interrupted`，会话可继续新输入（`pendingWake` 不丢失） |
| F5 | 回滚 | `POST /api/chat/{sid}/revert` 到某条消息 | 消息截断，且 `session_context_epoch.baseline_seq` 同步回滚，被撤销消息不再进入模型视角 |
| F6 | 压缩 | 长对话触发 `compaction_threshold` | 出现 `step_start/step_end`（compaction）事件，消息条数下降，后续轮次正常 |
| F7 | 多 Agent | `POST /api/chat/stream`（mode=multi-agent） | 主会话 + 子会话互不串扰；子会话经 task_bridge 隔离；超时参数（sub_agent_timeout=150s / supervisor_timeout=300s）生效 |
| F8 | 并发 | 同时开 3+ 个会话 | 全局并发上限内串行调度；`/stream/status` 的 `active_sessions + count_pending` 数值正确 |
| F9 | 标题生成 | 新会话触发 | `/compact`、标题接口基于新 repository 正常 |

### 5.2 Token 预算验证（P8）

| # | 用例 | 操作 | 预期结果 |
|---|---|---|---|
| T1 | 估算校正 | 长工具循环对话，观察 trace 日志 | 估算 ≈ 实际（校正系数 1.13 生效）；不再出现"实际 25,779 > usable"式越限 |
| T2 | 压缩提前介入 | 对话直至触发压缩 | 压缩在 `usable × 0.6` 处触发（而非 0.8），压缩后下一轮不再超限 |
| T3 | 收尾闭环 | 触发 MAX_STEPS 收尾路径 | 日志出现 `graph.final_round_start/ready`，且先 prune → compact → truncate 再注入收尾提示 |
| T4 | 截断兜底 | 极端长上下文 | `_truncate_messages` 兜底生效，单次调用不超 usable |

### 5.3 单元测试补充（建议）

- [ ] coordinator：并发上限、pendingWake 合并/不丢失、interrupt→stopping 路径；
- [ ] repository：seq 原子递增、revert 时 baseline_seq 回滚、parts 回填；
- [ ] budget：`compaction_threshold_tokens=0` 时按 `usable × ratio` 计算。
- 若已有 pytest 目录，`pytest -q` 全量跑一遍。

---

## 六、回滚方案

### 6.1 补丁回滚（P8）

```bash
python token_patch/fix_token_budget_p8.py --rollback   # 从 *.bak_p8 恢复
python token_patch/fix_token_budget_p8.py --verify     # 期望：全部"未应用"
```

> 注意：`--rollback` 与 `--apply` 不要混用混跑（脚本会提示），确认状态后再操作。

### 6.2 代码回滚（session 三层）

- 有 git：`git revert <pre-upgrade 提交>` 或 `git checkout -- app/session app/api/chat.py`。
- 无 git：用 Step 1 的整目录备份恢复。

### 6.3 数据回滚

- 用 `data/agentsuper.db.bak_pre_session_p8` 覆盖回滚；新表数据（session_inputs / context_epoch / parts）随库文件一并回退。

---

## 七、遗留问题与风险

| # | 风险/遗留 | 级别 | 处理建议 |
|---|---|---|---|
| R1 | `--verify` 尚未执行，P8 补丁应用状态未知 | 高 | 升级时先 `--verify`，再决定 apply |
| R2 | `max_context_tokens` 注释(24K)与代码(32K)不一致 | 中 | Step 3 二选一，统一口径 |
| R3 | `init_db()` 对旧库的迁移路径（seq 回填、建表）未验证 | 中 | 升级前用小库演练；必要时补迁移脚本 |
| R4 | 运行时端到端（SSE 事件顺序、interrupt、multi-agent）未实机验证 | 高 | 按第五节清单逐项验证后再切生产 |
| R5 | `_default_executor` 占位"promote 不消费"风险 | 中 | 升级后确认所有入口都经真实 executor；保留告警日志 |
| R6 | 前端适配（X-Session-Id 注入、done/error 事件内 tokens/usage 字段）未确认 | 中 | 与前端联调 F3 / F4 |
| R7 | 设计文档 P2 章节未更新 session 模块架构图 | 低 | 升级完成后补画调用链 |

---

## 八、附录

### 8.1 SSE 事件协议（/api/chat/stream）

```
queued        → 已入队（含会话/请求元信息）
message_start → 消息开始（含 message_id）
text / part   → 文本增量 / 部件增量（PartBridgeQueue 回填）
step_start / step_end → 步骤事件（含 compaction）
done / error  → 结束（done 含 tokens/usage 统计；error 含 classify_error 分类）
```

### 8.2 关键配置速查

```python
token_estimate_correction      = 1.13    # P8 低估校正
max_context_tokens             = 24_000  # 目标（当前代码 32_000，待统一）
context_reserve_tokens         = 8_192   # usable = max_context - reserve ≈ 15.8K（24K 时）
compaction_threshold_ratio     = 0.6     # 压缩提前介入（补丁脚本兜底默认 0.65，实际读 config）
context_tail_turns             = 2
context_preserve_recent_tokens = 8_000
tool_output_protect_tokens     = 24_000
tool_output_prune_minimum_tokens = 12_000
llm_max_tokens                 = 8_192
```

### 8.3 相关文件索引

```
token_patch/fix_token_budget_p8.py     # P8 补丁脚本（apply/verify/rollback）
token_patch/TOKEN_OPTIMIZATION_REPORT.md
token_patch/PATCH6_CHANGELOG.md
app/session/  (models/repository/coordinator/service/agent_executor/deps/task_bridge/db/history/ids/router)
app/api/chat.py
app/context/budget.py、token_counter.py、compaction.py、tool_output.py
app/agent/graph.py、supervisor.py
app/config.py
```

---

*文档结束。升级后请将验证结果回填第五节清单，并更新设计文档 P2 章节。*
