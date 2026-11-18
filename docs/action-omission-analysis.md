# Action Omission 问题分析与实现报告

> 范围：会话 `ses_1466d545ceaa417d`，用户请求“帮我把这个代码写到 `C:\tool\code\dpn-platform-bciblp` 目录下”。
> 状态：根因已定位；已实现“任务门禁 + 前置约束 + 事后校验 + 任务级结构化工具调用回退 + harness 指标/事件 + 前端未执行警告卡”，fake model 单测、前端单测与全量回归通过。
> 证据来源：`C:\Users\Administrator\AppData\Local\agentsuper\session.db`、`C:\Users\Administrator\AppData\Local\agentsuper\logs\litellm-debug.log`、`backend/graph/nodes/base.py`。

## 1. 结论摘要

任务不是在网络/SSE 层“断掉”，也不是权限拒绝、工具执行失败或工具表丢失，而是：

1. 模型本轮只生成了“承诺执行”的纯文本，没有产生任何 `tool_calls`。
2. Tool loop 按既有设计把“非空且无 tool_calls 的文本”当作最终答案，直接返回。
3. 上层因此记录 `finish=stop` + `execution.succeeded`，前端看起来是正常完成，但文件实际未写入。

这是一个 **Action Omission（意图与动作缺失）** 失败模式：模型说“我将执行”，但没有执行动作；平台原本没有二次校验“承诺动作与工具证据是否一致”。

当前修复已在 `run_tool_loop` 加入任务门禁：只有识别到写入/修改/删除类任务且存在相应工具时，才启用前置约束和事后校验；普通问答完全走原路径。若同时满足门禁任务且配置了本地 Ollama 模型，会先在首轮用 JSON schema 约束输出工具调用并强制走非流式，解析成功即进入工具执行；若模型仍只承诺执行，则按配置上限 nudge；最终仍无 mutating 工具证据时返回明确的“未执行”提示，将回合标记为 `execution.failed`，持久化一条 warning 并在前端时间线显示警告卡，不再伪装成成功答案。

## 2. 现象与证据

### 2.1 用户请求与模型输出

用户消息（event seq 56）：

```text
帮我把这个代码  写到 C:\tool\code\dpn-platform-bciblp  目录下
```

模型最终输出（event seq 61）：

```text
好的，我会将提供的 Java 冒泡排序代码写入到 `C:\tool\code\dpn-platform-bciblp` 目录下。为了确保代码正确写入，我将创建一个名为 `BubbleSort.java` 的文件，并将代码写入其中。

首先，我将创建目录 `C:\tool\code\dpn-platform-bciblp`，然后将代码写入该目录下的 `BubbleSort.java` 文件中。

请稍等片刻，我将执行这些步骤。
```

目标目录并非不存在：同一会话 seq 34–35 已经通过 `ls` 工具真实列出过 `C:\tool\code\dpn-platform-bciblp` 的内容（`.idea/`、`iblp-common/`、`pom.xml` 等）。模型仍然说“创建目录”，说明它没有有效利用已有工具证据。

### 2.2 事件时间线

| seq | 事件 | 说明 |
|----:|------|------|
| 56 | `session.inbox.enqueued` | 用户请求写入代码 |
| 58 | `session.inbox.delivered` | 请求投递 |
| 59 | `session.step.started` | 回合开始 |
| 60 | `session.text.started` | 模型开始输出正文 |
| 61 | `session.text.ended` | 输出“我将执行……” |
| 62 | `session.step.ended finish=stop` | 没有工具调用，正常终止 |
| 63 | `session.execution.succeeded` | 上层按成功收尾 |

中间不存在 `session.tool.called` / `session.tool.success` / `session.tool.error`。因此不是工具执行失败，也不是审批等待。

### 2.3 LiteLLM 日志证据

`litellm-debug.log:1395`：请求确实携带了 `tools`（`append_file`、`apply_patch`、`ask_question` 等），说明工具表已下发。

`litellm-debug.log:1418-1524`：所有 streaming delta 都是：

```text
tool_calls=None
```

且内容全是自然语言文本，最后自然结束。没有出现 `tool_calls=[...]`，也没有 `finish_reason=tool_calls`。

因此根因在模型输出侧，不在工具注册、传输或执行侧。

## 3. 当前设计与失效点

### 3.1 当前 agent loop

当前实现是 OpenAI 风格的 native tool-calling loop，本质上接近 ReAct：

```text
model → tool_calls? → execute → backfill → model → ... → text final answer
```

关键代码路径：

- `backend/graph/nodes/base.py:967`：`if not result.tool_calls:`
- `backend/graph/nodes/base.py:1023-1037`：解析 answer envelope，文本非空则直接 return
- 上层据此发出 `step.ended finish=stop` 与 `execution.succeeded`

### 3.2 已有纠偏机制

原有 loop 已覆盖：

- 空回复 nudge：`_NUDGE_EMPTY_ANSWER`
- 把工具调用写成 JSON 正文：`_NUDGE_NOT_A_TOOL_CALL`
- 输出长度截断：`_NUDGE_CONTINUE_LENGTH`
- 需要读文件证据但无 tool_trace：`_NO_TOOL_EVIDENCE_TEXT`

原实现**没有覆盖**本例：

> 文本非空、不是 JSON、语义上承诺执行工具，但实际没有 tool_calls。

这类输出原本被当作合法最终答案直接返回。

### 3.3 已实现的任务门禁链路

```text
识别写入/修改/删除任务
  → 检查本轮是否有 mutating 工具（write_file/edit_file/delete_file/shell_exec 等）
  → 有：注入一次性 system 门禁提示，禁止只描述步骤
  → 模型无 tool_calls 但承诺执行：追加一次 verifier nudge
  → 仍无 mutating 工具证据：返回 _ACTION_NOT_EXECUTED_TEXT
  → 普通任务 / 无可用写工具：不进入该链路
```

关键实现：

- `_ACTION_TASK_RE`：任务门禁，只匹配写入/修改/删除类意图。
- `_ACTION_GATE_SYSTEM_NOTE`：任务级前置约束，只在该门禁命中且工具可用时注入。
- `_promises_action()`：事后 verifier，只识别“我将/请稍等/接下来 + 执行类动词”等承诺话术。
- `_has_action_evidence()`：只把 mutating 工具调用视为写任务证据；`ls` 等读工具不算完成。
- `action_nudges < harness_action_omission_nudge_max`：默认最多纠正一次，可通过配置调整或关闭，避免弱模型把回合拖到上限。
- `_ACTION_NOT_EXECUTED_TEXT`：连续失败后的明确未执行提示，替代假成功。

### 3.4 为什么 3B 更容易触发

- 同一会话中短任务 `ls` 能成功发 tool call（seq 14、24、34），说明模型不是完全不会用工具。
- 本次任务要求把整段 Java 代码塞进 `write_file.content`，还要处理相对引用“这个代码”、路径、文件名、是否创建目录；对 3B 而言组合难度高。
- 模型退化为“叙述计划”而非“发出动作”，同时忽略了前面已有的 `ls` 工具证据。
- 请求里同时携带约 20 个工具 schema，弱模型更容易在长 schema + 长上下文下选择纯文本。

### 3.5 已实现的结构化回退链路

```text
门禁任务 + mutating 工具可用 + 当前无工具证据
  → 默认仅本地 Ollama 模型启用
  → effective_params 注入 response_format(json_schema)
  → 该轮强制非流式，避免 schema JSON 被当作正文泄露
  → 解析 {"name": ..., "arguments": ...} 为 ToolCall
  → 执行工具后回到原有 tool loop
  → schema 调用失败（非上下文溢出）则降级为原始参数重试
```

- 解析出的 tool call 使用统一 `stable_call_id()`，与 native/fallback 路径的确定性 id 一致。
- 结构化只用于工具决策轮；工具结果后的最终答复仍可流式输出。
- 配置开关：`harness_structured_tool_call_fallback_enabled`、`harness_structured_tool_call_fallback_local_ollama_only`。

### 3.6 可观测性与用户可见语义

- `observability.monitor.record_harness_signal()` 新增 5 类治理信号，并进入 `get_stats()` / `get_recent_stats()`（`/api/monitor/stats`）：
  - `structured_attempt`：任务门禁命中且注入了 `response_format`。
  - `structured_hit`：结构化 JSON 成功解析为至少一个 `ToolCall`。
  - `structured_fallback_degraded`：schema 调用失败（`reason=call_error`）或输出不可用（`reason=no_parsed_call`）。
  - `action_nudge`：承诺动作但未调用工具，触发一次纠偏。
  - `action_not_executed`：最终确认无 mutating 工具证据，返回未执行提示。
- `run_tool_loop` 返回 `action_not_executed` 布尔标记，经 `execute` 节点写入 `AgentState`。
- `ExecutionCoordinator` 检测到该标记后：
  - 把回合终态改为 `session.execution.failed`（`error.type=action_not_executed`），不再记录 `execution.succeeded`。
  - 持久化一条 `type=synthetic`、`description=action_not_executed` 的稳定 warning 消息，保证 hydration 后仍可见。
  - 发出 durable `session.synthetic` 事件供当前连接即时渲染。
- 前端 `V2ChatView` 对 synthetic 消息渲染 `.v2-warning-card`，`v2/runtime.ts` 按 `metadata.warning_id` 幂等 upsert。

## 4. 成熟的行业方案

该失败模式在业界通常不靠“再写一条 prompt”解决，而是靠以下成熟模式组合：

| 方案 | 代表实现/思路 | 对本问题的适配度 |
|------|--------------|------------------|
| ReAct / Tool Calling | LangGraph ReAct、OpenAI Assistants/Agents、Anthropic Tool Use | 当前已采用；优点是通用，缺点是依赖模型稳定发 tool call |
| Reflection / Verifier | Reflexion、self-critique、post-hoc verifier | 高：检测“承诺动作但零工具调用”，失败则 nudge 或拒答 |
| Plan-and-Execute | Planner 拆步骤，Executor 小上下文单步执行 | 高：把“写代码 + 选路径 + 调用工具”拆成更小的执行单元 |
| 结构化输出 / 约束解码 | JSON schema、regex grammar、prefill tool tag | 高：Ollama 不支持 `tool_choice` 强制，但支持 `format`；社区验证 `<tool_call>` pattern 对 qwen2.5-coder 有效 |
| 能力路由 | 按模型工具能力路由任务 | 中高：写文件/多步任务避开 3B，交给 7B+/coder |

补充事实：Ollama 官方兼容文档当前把 `tool_choice` 列为不支持；社区 issue #14967 也确认没有机制强制模型调用工具，只能靠 prompt、prefill 或 structured output 影响模型。

## 5. 方案对比与影响

### 方案 A：仅提示用户换模型

- 改动：无。
- 影响：用户需要自己识别失败并重发；平台仍会记录假成功。
- 风险：体验与可信度继续受损。
- 结论：只能作为即时规避，不算修复。

### 方案 B：任务门禁 + Action-omission guard（已实现）

在 `run_tool_loop` 中按任务类型启用，而不是全局启用：

```text
写入/修改/删除任务
AND 本轮存在 mutating 工具
  → 注入 system 门禁提示
  → 无 tool_calls 且文本承诺执行：nudge 一次
  → 仍无 mutating 工具证据：返回明确“未执行”提示
```

- 改动范围：`backend/graph/nodes/base.py` 增加门禁、verifier 与 nudge 常量；新增 `backend/tests/unit/test_action_omission_guard.py`。
- 影响：
  - 仅失败时额外一次模型调用；普通问答零额外调用。
  - 不改 SSE contract、不改 DB、不改前端、不改 prompt 文件与 golden hash。
  - 读工具（`ls`）不再被当作写任务完成证据。
- 风险：
  - 误伤“我将总结一下”这类话术。已通过三层门禁规避：写任务意图 + mutating 工具可用 + 承诺动作正则，且最多 nudge 1 次。
  - 无可用写工具时不进入门禁，保持原行为，避免无效 nudge。
- 结论：低成本、可回滚，作为第一阶段已落地。

### 方案 C：任务级 structured tool-call 回退（已实现）

在门禁任务且尚无工具证据的回合，对本地 Ollama 注入 `response_format` JSON schema，把输出约束为单个 tool call，再解析成统一 `ToolCall` 进入原 tool loop。

- 改动范围：`backend/graph/nodes/base.py`、`backend/config.py`、`backend/config.example.json`，新增 `backend/tests/unit/test_structured_tool_call_fallback.py`。
- 影响：
  - 可靠性更高，直接减少弱模型“叙述代替调用”。
  - 仅在任务门禁命中时启用；普通问答不会被逼成 tool JSON。
  - 结构化轮次强制非流式，避免 JSON 进入正文流；工具结果后的最终答复仍保持流式。
  - 默认 `harness_structured_tool_call_fallback_local_ollama_only=true`，避免对云端/非 Ollama provider 擅自注入 `response_format`。
- 风险：
  - 若模型输出 schema 外文本或 schema 调用失败，仍依赖 B 方案 guard 兜底；非 overflow 失败会降级为原始参数重试。
  - 真实本地 3B 模型的兼容性与命中率仍需实测；当前只完成 fake model 验证。
- 结论：作为第二阶段已落地，与 B 方案形成“先约束、再纠偏、最后明确未执行”的链路。

### 方案 D：能力路由 / Plan-and-Execute

- 改动范围：catalog 能力标注、路由策略、可能的 planner 节点。
- 影响：
  - 写文件/多步任务切到 7B+/coder，延迟和资源上升。
  - 用户可见的模型选择行为会变化。
- 风险：能力标注错误会过早排除可用模型。
- 结论：长期方向；短期不建议和 guard 同时上，以免变量过多。

## 6. 落地状态与后续顺序

1. **已实现**：方案 B（任务门禁 + 前置 system 约束 + 事后 verifier + 可配置 nudge + 明确未执行提示）。
2. **已实现**：方案 C（任务级 structured tool-call 回退，默认仅本地 Ollama，非流式决策轮，统一 tool id）。
3. **已实现**：治理信号（nudge / 未执行 / 结构化尝试 / 命中 / 降级）进入 monitor stats；失败回合标记 `execution.failed`。
4. **已实现**：前端 warning 卡与持久化 synthetic warning 消息，避免 hydration 后丢失。
5. **待实测**：用真实本地 3B/Ollama 验证 schema 命中率、参数质量与多工具场景。
6. **长期（未实现）**：结合方案 D 做能力路由与 Plan-and-Execute 任务拆分。

## 7. 验证结果

- 新增 `backend/tests/unit/test_action_omission_guard.py`、`backend/tests/unit/test_structured_tool_call_fallback.py`，全部使用 fake model 模拟模型，不调用真实/本地模型。
- 新增 `backend/tests/unit/test_v2_warning_message.py`、`backend/tests/unit/test_session_coordinator.py` 用例，验证 warning 持久化映射与失败终态语义。
- 新增 `frontend/tests/unit/contracts/v2Runtime.spec.ts` 用例，验证 synthetic warning 的稳定 id 与幂等 upsert。
- Guard 覆盖场景：
  - 承诺文本 → nudge → `write_file` tool call → 工具执行并按工具结果收尾。
  - 连续承诺文本 → 最多一次 nudge → 返回“还没有实际执行”提示。
  - “我将简要说明”类普通问答 → 不进入门禁，不 nudge。
  - “生成一段代码”未指定文件目标 → 不进入门禁。
  - 只有 `ls` 无 mutating 工具 → 门禁不启用，不 nudge。
  - 已有读工具证据但无写工具证据 → 仍提示未执行。
  - guard 总开关关闭、`nudge_max=0`、`nudge_max=2` 的配置行为。
- Structured fallback 覆盖场景：
  - 本地 Ollama 注入 `response_format` 并把 JSON 解析为 `write_file` tool call。
  - 结构化决策轮强制非流式，工具结果后的最终答复仍流式。
  - 关闭结构化回退后不注入 `response_format`。
  - 默认限制非本地模型不注入；关闭限制后可注入。
- 可观测性 / 语义覆盖场景：
  - `action_nudge`、`action_not_executed` 信号写入 monitor stats。
  - `run_tool_loop` 返回 `action_not_executed` 标记。
  - coordinator 在失败时发出 `session.execution.failed` 与 `session.synthetic`，并把 session outcome 设为 `failed`。
  - synthetic warning 映射为 v2 message；前端按 warning id 幂等渲染。
- 运行命令与结果：
  - `.venv\Scripts\python.exe -m pytest tests/unit/test_action_omission_guard.py tests/unit/test_structured_tool_call_fallback.py tests/unit/test_session_coordinator.py tests/unit/test_v2_warning_message.py tests/unit/test_config_example.py -q` → 全通过。
  - `.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/reliability tests/security -q` → 全量通过，仅 pydantic `ReadOnly` warning。
  - `npm test` → `100 passed | 118 skipped (218)`。
  - `npm run build` → 通过。

## 8. 仍待确认问题

1. 是否用真实本地 3B/Ollama 做一轮兼容性验收？当前严格使用 fake model，未调用真实模型。
2. 结构化回退是否扩展到多 tool 选择、每工具严格参数 schema 与非本地 provider 支持矩阵？当前仅任务门禁 + 本地 Ollama + 松散单工具 schema。
3. 是否把能力路由 / Plan-and-Execute 纳入后续排期？当前未改模型选择行为。

## 9. 附录：关键引用

- Session DB：`C:\Users\Administrator\AppData\Local\agentsuper\session.db`
- LiteLLM 日志：`C:\Users\Administrator\AppData\Local\agentsuper\logs\litellm-debug.log`
  - `:1395` 请求携带 tools
  - `:1418-1524` 全部 delta `tool_calls=None`
- 代码：
  - `backend/graph/nodes/base.py:125` `_NUDGE_ACTION_OMISSION`
  - `backend/graph/nodes/base.py:130` `_ACTION_NOT_EXECUTED_TEXT`
  - `backend/graph/nodes/base.py:134` `_ACTION_GATE_SYSTEM_NOTE`
  - `backend/graph/nodes/base.py:171` `_needs_action_evidence()` 任务门禁
  - `backend/graph/nodes/base.py:196` `_structured_response_format()`
  - `backend/graph/nodes/base.py:215` `_parse_structured_tool_calls()`
  - `backend/graph/nodes/base.py:254` `_is_local_ollama_model()`
  - `backend/graph/nodes/base.py:937` 任务门禁组装
  - `backend/graph/nodes/base.py:962` `_signal()` / harness 指标入口
  - `backend/graph/nodes/base.py:1016` `structured_round` / `response_format` 注入
  - `backend/graph/nodes/base.py:1159` 结构化 JSON 解析为 `ToolCall`
  - `backend/graph/nodes/base.py:1172` 无 tool_calls 分支
  - `backend/graph/nodes/base.py:1243` `action_nudge_max` 上限
  - `backend/observability/monitor.py:240` `record_harness_signal()`
  - `backend/graph/state.py:62` `action_not_executed` 状态字段
  - `backend/graph/nodes/execute.py:349` 状态传播
  - `backend/app/session/coordinator.py:96` 失败回合标记消费
  - `backend/app/session/coordinator.py:408` warning 持久化 / durable 事件
  - `backend/app/api/v2/session.py:338` synthetic message 映射
  - `frontend/src/v2/runtime.ts:516` warning 幂等 upsert
  - `frontend/src/views/V2ChatView.vue:1294` warning 卡
  - `frontend/src/styles/v2Chat.css:64` `.v2-warning-card`
  - `backend/config.py:461-464` 新增配置字段
  - `backend/config.example.json:660-663` 配置示例
  - `backend/tests/unit/test_action_omission_guard.py` guard fake model 单测
  - `backend/tests/unit/test_structured_tool_call_fallback.py` structured fallback fake model 单测
  - `backend/tests/unit/test_v2_warning_message.py` warning 映射单测
  - `frontend/tests/unit/contracts/v2Runtime.spec.ts` warning reducer 单测
