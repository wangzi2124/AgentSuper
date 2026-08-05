# AgentSuper 与 opencode 聊天信息设计 差距分析

> 参照文档：`docs/opencode-chat-message-design.md`
> 分析对象：AgentSuper `backend/app/session/` + `backend/app/api/chat.py` + `frontend/src/types/index.ts`
> 结论先行：AgentSuper 已在「会话隔离 / 追加日志 / 上下文纪元 / 输入队列」上对齐，但**核心的消息内容模型仍是「扁平 content」而非「消息骨架 + Parts」**，这是与设计文档最大的差距。

---

## 1. 现状速览（已对齐的部分）

| 设计文档章节 | AgentSuper 现状 | 状态 |
|-------------|----------------|------|
| Session → Message → Part 三层 | `sessions` / `session_messages` / `message_parts` 三表齐备 | ✅ 表已建 |
| 追加式事件日志 + per-session `seq` | `session_messages.seq`，`BEGIN IMMEDIATE` 内 `MAX(seq)+1` 原子计算 | ✅ 完全一致 |
| `session_context_epoch` 压缩基线 | 表 + `history.load` 按 `max(epoch.baseline_seq, compaction_seq)` 过滤 | ✅ 一致 |
| `session_inputs`（steer/queue） | `admit_input`/`promote_next`/`has_pending` | ✅ 一致 |
| 会话隔离 / 子会话 | `project_id`/`workspace_id`/`parent_id`，`kind='task'` 子会话 + 级联删除/打断 | ✅ 一致 |
| Revert 级联 | `revert_to_message` 删除消息+parts，回滚 epoch 水位，清 inputs | ✅ 一致 |
| ID 前缀 `ses_`/`msg_` | `ses_`/`msg_`（part 用 `pt_`，与 `prt_` 不同） | ⚠️ 前缀近似但语义不同（见 G2） |

---

## 2. 差距清单

### G1【最大差距】消息是「扁平 content」，不是「骨架 + Parts」

**设计文档**：Message 只存骨架（role / time / parentID / model / tokens / cost / finish / error），正文全部拆成异构 Part（text / reasoning / tool / step-start / step-finish / snapshot / patch / …）。

**AgentSuper 现状**：
- `session_messages.data` 直接存 `{"content": "整段文本", "sources": [], "steps": []}`（`agent_executor.py:147`、`chat.py:383`）。
- `message_parts` 表建好了，但**主流程从不写入**——`append_part`/`list_parts` 仅被 `service.py:154`（fork 复制）调用。

**影响**：
- 一条助手回复里的「逐步文本 + 推理 + 多个工具调用 + step 边界 + 快照」无法结构化表达，全部混成一段 `content`。
- 无法做 Part 级增量推送 / 局部更新 / 断线重连重建中间过程。
- 历史「模型视角」与「渲染视角」共用同一扁平结构，无法分别裁剪（如模型视角裁掉工具输出，渲染视角保留）。

**工作量**：中～大。需要引入 Part 类型体系 + 改写落库路径。

---

### G2 ID 不可排序，part 前缀不一致

**设计文档**：`msg_`/`prt_` 前缀 + 12 位时间戳（`timestamp*0x1000+counter`）+ 随机段，**单调递增、可直接排序**；SessionID 倒序。

**AgentSuper 现状**：
- `msg_{uuid4().hex[:24]}`（`repository.py:245`）、`pt_{uuid4().hex[:24]}`（`repository.py:311`），完全随机。
- 排序依赖 `time_created` 与 `seq`；`list_parts` 用 `ORDER BY time_created`（`repository.py:328`）——同毫秒创建的两个 part 顺序不保证。
- Part 前缀是 `pt_`，opencode 是 `prt_`。

**影响**：
- 分页游标、part 顺序、fork/revert 边界判定缺少确定性；未来要实现 opencode 式 `cursor={id, time}` 分页时，`time_created+id` 比较仍可用，但 `id` 本身不可比。

**工作量**：小。写一个带时间戳的 ID 生成器替换 `uuid4`（保留 `msg_` 前缀可兼容存量数据），part 前缀改 `prt_`（需评估存量 `pt_` 数据兼容）。

---

### G3 工具调用/步骤没有 Part 级结构与状态机

**设计文档**：Tool Part 有 `state: pending → running → completed/error`（含 `time.start/end`、`input`、`output`、`metadata`、`attachments`）、`step-start`/`step-finish` Part 带快照与 tokens。

**AgentSuper 现状**：
- `steps` 只是 assistant 消息 `data.steps` 里的**一次性数组快照**（`agent_executor.py:151`），无状态机、无时间语义、不可回放。
- 流式事件（`step_start`/`tool_start`/`tool_end`/`tool_output`）实时推给前端，但**不持久化**。

**影响**：
- 刷新页面后中间过程丢失，只能显示最终 steps 列表。
- 无法实现 opencode 那样的「工具状态卡片」渐进式更新（pending→running→completed）。

**工作量**：中。把 executor 里 agent 事件桥的 step/tool 事件改写为 `append_part`（tool/step-start/step-finish），并让 `_message_to_history` 从 parts 重建模型历史。

---

### G4 assistant 消息缺结构化结算元数据（tokens/cost/finish/error/parentID）

**设计文档**：assistant 骨架含 `parentID / modelID / providerID / mode / agent / path{cwd,root} / cost / tokens{input,output,reasoning,cache} / finish / error / summary`。

**AgentSuper 现状**：
- assistant 消息只存 `content/sources/steps/agents`（`agent_executor.py:147`）。
- `tokens_input/output/cache` 汇总在 `sessions` 表（`db.py:51`），但**消息级不落库**；`finish`（stop/tool-calls/length/…）、结构化 `error`（APIError/AuthError/ContextOverflow…）均无。

**影响**：
- 无法做每条消息级 token/费用统计、失败原因归一、`finish_reason` 语义（例如依据 `tool-calls` 决定循环是否继续的回放逻辑）。

**工作量**：小～中。在 assistant data 里补充 tokens/cost/finish/error/parentID 字段（graph.py 已产出部分数据）。

---

### G5 Part 类型体系缺失（判别式 union 未落地）

**设计文档**：12 种 Part（text / reasoning / file / tool / step-start / step-finish / snapshot / patch / agent / subtask / retry / compaction），按 `type` 判别，各有专用字段。

**AgentSuper 现状**：`Part = {id, session_id, message_id, type, data}`，`type` 无约束、无字段结构，`message_parts` 几乎闲置。

**影响**：承接 G1/G3 后，需要为各 Part type 定义明确的 `data` 结构（可在 pydantic 层做，不必照搬 effect Schema）。

---

### G6 事件流无 Part 级增量（delta）

**设计文档**：`message.part.updated`（整条 part 更新）+ `message.part.delta`（`{partID, field, delta}` 增量，用于流式文本/推理累积）。

**AgentSuper 现状**：SSE 事件是 `step_start/step_end/tool_start/tool_end/agent_step/done`，**无 partID 概念**；流式文本通过事件 + 前端本地拼装，增量不持久化。

**影响**：刷新/断线后无法恢复打字过程；前端要渲染 Part 需要事件带 partID 定位。

**工作量**：中。结合 G3 在落库 part 的同时推送 part 事件。

---

### G7 compaction 机制是「摘要快照」而非「重排 + tail 保留」

**设计文档**：compaction = user 消息 + `compaction` part（`auto`/`overflow`/`tail_start_id`）+ `summary:true` 的 assistant 摘要消息，读取时 `filterCompacted` 重排为 `[compaction, summary, ...tail..., continue]`。

**AgentSuper 现状**：compaction 是一条 `type='compaction'` 的系统消息（存 checkpoint 文案），`history.load` 把它作为 system 上下文带回（`history.py:44`）；基于 SummarizationMiddleware 的分层摘要，无 tail 保留。

**影响**：语义不同但目的等价（压缩历史 + 保留摘要）。除非要实现「保留最近 N 轮原文」的精确回放，否则**可不改**。若追求对齐，可引入 `tail_start_id` + 重排。

**工作量**：可不做；做则中。

---

### G8 前端消息模型扁平

**设计文档**：前端按 Message 骨架 + Parts 渲染（step-start 分块、text 气泡、reasoning 折叠、tool 状态卡、patch 文件变更）。

**AgentSuper 现状**：`Message {id, role, content, steps?}`（`frontend/src/types/index.ts:211`），`MultiAgentMessage` 同样扁平；无 Part 概念、无工具状态卡、无流式增量定位。

**影响**：后端改了结构，前端不跟着改就无法展示新能力。

**工作量**：中～大（取决于后端改到什么程度）。

---

## 3. 建议的调整路线（按优先级）

### 阶段 A：最小对齐（改动小、不动存储主路径，收益立即可见）
1. **G2** 引入带时间戳的可排序 ID 生成器（`msg_`/`prt_` 前缀），part 前缀统一为 `prt_`；`list_parts` 改按 `(message_id, id)` 排序。
2. **G4** assistant 落库补充结构化字段：`parent_id`、`model_id`、`provider_id`、`agent`、`cost`、`tokens{input,output,reasoning,cache}`、`finish`、`error`（graph.py 已产出大部分）。
3. **G5 子集** 为 `message_parts.data` 定义各 type 的 pydantic 结构（text/tool/step/reasoning/file/patch/compaction）。

### 阶段 B：把聊天主路径切到 Parts（对齐设计文档的核心）
4. **G1+G3+G6** 重写 executor 落库：agent 事件桥（step_start/step_end/tool_start/tool_end/文本增量）→ `append_part`（`step-start`/`step-finish`/`tool`/`text`/`reasoning`），assistant 消息只存骨架。
5. `_message_to_history` / `_session_history_for` 改为从 parts 重建模型历史（文本 part → 内容，tool part → 工具结果，忽略 step-start 等控制 part）。
6. 前端 `Message` 增加 `parts`，渲染 tool 状态卡 + 增量文本；SSE 事件带 `part_id`。

### 阶段 C（可选）
7. **G7** 实现 `tail_start_id` 重排式 compaction（保留最近 N 轮原文）。

> 建议：先做**阶段 A**（1–2 天量级、可增量、不破坏现有数据），评估后再决定是否进入阶段 B。阶段 B 会改动消息存储格式，需要迁移/兼容策略。

---

## 4. 关键代码位置速查

| 位置 | 说明 |
|------|------|
| `backend/app/session/db.py:65-86` | `session_messages` / `message_parts` 表 |
| `backend/app/session/models.py:46-67` | `Message`（扁平 content）/ `Part` 模型 |
| `backend/app/session/repository.py:235-336` | `append_message` / `append_part` / `list_parts` |
| `backend/app/session/agent_executor.py:147-151` | assistant 落库（content/steps 一次性快照） |
| `backend/app/session/history.py:25-49` | 模型视角历史装载 |
| `backend/app/session/service.py:154` | fork 复制 parts |
| `backend/app/api/chat.py:907-921` | `/conversations/{id}` 返回扁平消息 |
| `frontend/src/types/index.ts:211-221` | 前端 `Message` 模型 |

---

## 5. 阶段 A 实施记录（2026-08-05）

已落地（对应第 3 节「阶段 A」）：

- **G2 可排序 ID**：新增 `backend/app/session/ids.py`（`<prefix><base36 时间编码><随机后缀>`，字典序即时间序）。`repository.py` 的 `create_session`/`append_message`/`append_part`/`admit_input` 改用 `new_id("ses_"|"msg_"|"prt_"|"in_")`，part 前缀 `pt_`→`prt_`；`list_parts` 排序改 `ORDER BY time_created, id`（兼容存量随机 `pt_` ID）。
- **G4 结算字段**：
  - `graph.py` 增加 token 用量累加（`_llm_call` 累加 `_usage_accum`，`_generate` 开头重置），`invoke` 返回新增 `model`/`finish`/`tokens`/`cost`；`AgentState` 增加 `tokens`/`finish` 键。
  - `agent_executor.py` assistant 落库补充 `parent_id`/`agent`/`model`/`finish`/`tokens`；新增 `repository.add_session_usage` 累加会话级 tokens/cost。
  - `chat.py:_persist_multi_agent` 同步补充 `parent_id`/`agent`/`model`。
- **G5 Part 结构**：`models.py` 新增 `PART_DATA_MODELS` 判别映射（text/reasoning/tool/step-start/step-finish/file/patch/agent/compaction）与 `TokenUsage`，供 `append_part` 校验 `data`。

已验证：模块可导入、ID 排序正确、repository 增删/part 顺序/usage 累加函数级测试通过。

### 阶段 B（2026-08-05）

已落地（对应第 3 节「阶段 B」核心）：

- **G1+G3+G6 聊天主路径切到 Parts**：
  - 新增 `PartBridgeQueue`（`agent_executor.py`）：graph 事件实时落库为 message_parts——`step_start`→`step-start`、`step_end`→`step-finish`、`tool_start`→`tool`(running)、`tool_end`→更新同 part 为 completed+output（tool 状态机）；`tool_output`/`tool_heartbeat` 仅转发不落库；转发的 SSE 事件带 `part_id`。
  - executor 重排：先建 assistant 骨架消息（含 `parent_id`/`agent`/`model`/`finish:"running"`）→ `agent.invoke(event_queue=bridge)` 实时落 parts → 回填 `content`/`finish`/`tokens`/`steps`/`sources` 结算字段 → `append_text(最终答案)`；异常时骨架标记 `finish:"error"` 防止空消息残留。
  - `done` SSE 事件携带 `parts`（最终 part 列表）。
  - repository 新增 `update_part`（tool 状态机更新）、`update_message`（结算字段回填）、`list_parts_for_messages`（批量加载）。
- **G3 历史重建**：`_message_to_history` / `_session_history_for` 改为优先取 text parts 拼内容，无 parts 时回退 `data.content`（兼容旧数据/compaction/system）。
- **G8 前端**：`Message.parts?: Part[]` + `Part` 判别类型；`/conversations/{id}` 返回 `parts`；ChatMessage.vue 优先渲染 parts（text 拼正文、tool part 状态卡、step-start/finish 合并步骤、reasoning 折叠块），SSE 事件类型加 `part_id`；IndexedDB 缓存 merge 时 `parts` 视为 meta 字段避免被旧缓存覆盖。

未做（阶段 B 之外）：多 Agent 子会话的 parts 落库（`_persist_multi_agent` 仍扁平）、模型视角的 reasoning part 参与、`message.part.delta` 真增量（graph 目前不流式输出 token）。

