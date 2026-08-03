# AI 助手上下文（Context）设计

> 参考 opencode 的智能体上下文设计，适配本项目的 **FastAPI + LangGraph + SQLite** 技术栈。
>
> opencode 核心设计（源码参考）：
> - 存储模型：`packages/core/src/session/sql.ts`（session / message / part，工具调用是 part）
> - 系统提示词分层装配：`packages/opencode/src/session/prompt.ts`（`env + instructions + mcp + skills`）
> - Token 预算：`packages/opencode/src/session/overflow.ts`（`usable = context − reserve`）
> - 三级回收：`packages/opencode/src/session/compaction.ts`（prune / select+锚定摘要 / truncate 兜底）
> - 锚定摘要模板：`packages/core/src/session/compaction.ts`（Objective / Important Details / Work State / Next Move / Relevant Files）
> - 主循环：`packages/opencode/src/session/prompt.ts`（`filterCompacted → isOverflow → compact → task 解析 → 循环`）
> - 压缩提示词：`packages/opencode/src/agent/prompt/compaction.txt`

---

## 1. 背景与现状

当前 `AgentSuper` 的上下文实现分散在：

| 位置 | 职责 |
| --- | --- |
| `backend/app/api/chat.py` | 会话级历史窗口：80K token 滑动截断（`MAX_HISTORY_TOKENS`）+ 可选分层摘要 |
| `backend/app/session/agent_executor.py` | Session 持久化路径：`history.load` → 分层摘要/截断 → 落库压缩基线 |
| `backend/app/agent/graph.py` `_generate` | LLM 主循环：截断到 `max_context_tokens`、工具调用循环、`ContextCompactor` 压缩 |
| `backend/app/context/token_counter.py` | tiktoken 计数、`truncate_messages`（固定预留 4096）、`sanitize_tool_messages` |
| `backend/app/context/compaction.py` | `ContextCompactor`：阈值 80K、保留最近 6 条、检查点式摘要 |
| `backend/app/context/tool_output.py` | 工具输出**入口**边界（行数/字节限制） |
| `backend/app/context/tool_dedup.py` | 单轮内相同工具调用的去重 |

### 与 opencode 的关键差异

| 维度 | opencode | AgentSuper（现状） | 问题 |
| --- | --- | --- | --- |
| Token 预算 | `usable = min(context, input) − min(20_000, maxOutputTokens)`，模型感知 | 固定 `max_context_tokens=24_000`，预留 4096 | 预算不随模型变；预留不足输出空间（completion `max_tokens=4096`，预留却只有 4096，无缓冲） |
| 压缩触发 | 运行到 **overflow**（接近预算）时触发 | 阈值 80K，但 truncate 上限 24K → **压缩永不触发**（死代码） | 长工具循环只能"丢弃"旧消息，不能"总结" |
| 尾部保留 | 按**轮次**（默认最后 2 个 user turn）+ token 预算（2K–8K） | 按**消息条数**（`keep_recent=6`） | 大工具结果可能把尾部预算撑爆；条数不反映信息量 |
| 摘要方式 | **锚定摘要**：增量更新 previous summary（保留仍真、删除过期、合并新事实） | 每次从零重写 | 多轮压缩间信息流失、重复计费 |
| 工具输出回收 | **回溯式 prune**：越过最近 2 轮，把超出 40K token 的旧工具输出在上下文中擦除 | 仅**入口** bounding，无回溯清理 | 已进入上下文的旧工具输出长期占据预算 |
| 主循环 | 循环内 `isOverflow → 自动压缩 → 自动续跑`（合成 user 消息驱动） | 固定 `max_tool_rounds=24`，压缩后在原循环继续 | 结构相近，但预算/触发错位导致压缩形同虚设 |
| 多 agent 事件 | 子 agent 事件回流主队列 | `RAGAgentWrapper` 不传 `event_queue` | 工具事件丢失 + 权限审批在无队列时永久等待（150s 超时兜底） |

---

## 2. 目标设计（适配 AgentSuper）

### 2.1 Token 预算（模型感知）

借鉴 `overflow.ts`：

```
max_context_tokens    # 模型上下文上限（.env: MAX_CONTEXT_TOKENS）
context_reserve_tokens  # 输出预留 = min(20_000, maxOutputTokens)，默认 8_192
usable = max(0, max_context_tokens − context_reserve_tokens)
```

- 截断（安全网）以 `usable` 为上限，而不是全量上下文。
- 新增 `backend/app/context/budget.py` 集中计算 `usable_context_tokens()` 与 `compaction_threshold_tokens()`。

### 2.2 压缩（轮次尾部保留 + 锚定摘要）

对齐 `compaction.ts:select` + `core/session/compaction.ts:buildPrompt`：

1. **should_compact**：`estimate_tokens_messages(messages) > compaction_threshold`，默认 `0.8 × usable`（在截断上限之前触发）。
2. **select（尾部保留）**：
   - 以 **user 消息划分轮次**，保留最后 `tail_turns=2` 轮、总预算 `preserve_recent_tokens=8_000`（2K–8K 之间）。
   - 超出预算的轮次**只保留该轮尾部**（`splitTurn`），分割点取"轮次边界"（user 消息开头或完整工具轮起点），避免切断 `tool_calls ↔ tool` 对应关系。
3. **锚定摘要**：
   - 从历史中提取最近的上一份 checkpoint（`[Task checkpoint` 标记），作为 `<previous-summary>` 传入。
   - 有 previous → "更新锚定摘要"；无 → "创建新锚定摘要"。
   - 输出模板对齐 opencode：`Objective / Important Details / Work State(Completed/Active/Blocked) / Next Move / Relevant Files`，输出上限 2048 token。
4. **兜底 truncate**：摘要失败或模型不支持时保留 system + 最近消息，插入哨兵。

### 2.3 工具输出回溯清理（prune）

对齐 `compaction.ts:prune`：

- 从最新消息往回走，越过最近 2 个 user turn；若累计**已完成**工具输出 token > `tool_output_protect_tokens`（默认 40_000），把更旧的工具结果内容替换为 `[tool output pruned …]` 桩。
- 仅当可清理总量 > `tool_output_prune_minimum_tokens`（默认 20_000）才真正落桩，避免微小收益的频繁改写。
- 与入口 `bound_tool_output` 互补：入口限大小、prune 回收存量。

### 2.4 循环整合（graph.py `_generate`）

每个工具轮：

1. `messages = prune_tool_outputs(messages)`（回收旧工具输出）
2. `if compactor.should_compact(messages): messages = await compactor.compact(messages)`
3. `sanitize_tool_messages`（删除被截断切断的孤儿 tool 消息 / 不完整工具轮）
4. `_truncate_messages(messages, max_tokens=usable, reserve_tokens=0)`（兜底，不二次预留）
5. `_llm_call`（`max_tokens` 输出由 `context_reserve_tokens` 保障）

### 2.5 多 agent 权限/事件（bug 修复）

`RAGAgentWrapper.handle_message` 调用 `inner.invoke()` 不传 `event_queue`，导致：

- 工具实时事件（`tool_start/tool_end`）丢失；
- `_execute_tool` 中 `NeedsPermission` 分支 `eq is None` 时仍 `await_decision` → 永久等待。

修复：`_execute_tool` 在无事件队列时直接返回 `Permission denied`（不再等待），多 agent 下权限审批不会死锁；完整事件回流留待后续把子 agent 事件桥接到主会话 SSE 队列。

### 2.6 配置项（`config.py`，均可 `.env` 覆盖）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `max_context_tokens` | 64_000（原 24_000） | 模型上下文上限 |
| `context_reserve_tokens` | 8_192 | 输出预留 |
| `compaction_threshold_tokens` | 0（=0.8×usable） | 压缩触发阈值 |
| `context_tail_turns` | 2 | 尾部保留轮次 |
| `context_preserve_recent_tokens` | 8_000 | 尾部保留 token 预算 |
| `tool_output_protect_tokens` | 40_000 | prune 保护下限 |
| `tool_output_prune_minimum_tokens` | 20_000 | prune 生效下限 |

### 2.7 分层边界（保持不变）

- **会话级持久窗口**（`chat.py` / `agent_executor.py`，80K + 分层摘要）：决定"存多少历史进库"，与模型无关。
- **单次请求上下文**（graph.py）：决定"每次 LLM 调用喂多少"，受模型预算约束。
- 两者解耦：持久窗口可以大于模型预算，模型侧再按 `usable` 安全截断。

---

## 3. 实施变更清单

- [x] `docs/context-design.md`：本文档
- [x] `backend/app/config.py`：新增 §2.6 配置项，`max_context_tokens` 默认 64_000
- [x] `backend/app/context/budget.py`：新增 `usable_context_tokens()` / `compaction_threshold_tokens()`
- [x] `backend/app/context/compaction.py`：轮次尾部保留 + 锚定摘要 + 阈值参数化 + 模板对齐
- [x] `backend/app/context/tool_output.py`：新增 `prune_tool_outputs()`
- [x] `backend/app/agent/graph.py`：接入预算/prune/压缩阈值；`_execute_tool` 无队列时拒绝权限请求
- [x] `backend/.env`：补充新配置注释示例

## 4. 验证方式

- 无测试框架；改动后用 `backend\.venv\Scripts\python.exe -m py_compile <files>` 做语法校验。
- 手动验证：长工具循环会话观察 `step_start(compaction)` 事件出现（此前永不出现）；多 agent 触发权限工具不再等待 150s。
