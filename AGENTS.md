# AGENTS.md — Knowledge Base System (Harness)

> 面向 coding agent 的工作手册。人类入口看 [`README.md`](README.md)。
> 架构权威：`docs/harness-architecture-design.md`；差距/验收：`docs/harness-design-gap-matrix.md`。
> 文档与实际代码冲突时，以当前代码 + 测试 + 设计文档为准，并同步修正本文件。

## 0. 先读什么

- **后端主链路**：`backend/app/main.py` → `backend/app/deps.py` → `backend/graph/main_graph.py` → `backend/graph/nodes/`
- **Harness 层**：`backend/harness/`（工具协议、TurnHarness、压缩、切换、预算、错误、安全）
- **前端主链路**：`frontend/src/views/MultiAgentView.vue` → `frontend/src/stores/multiAgent.ts` → `frontend/src/harness/`
- **测试**：`backend/tests/`、`frontend/tests/`
- 旧的 `backend/app/agent/**`、`backend/app/context/**`、`backend/scripts/**` **已删除**，不要按旧路径找代码。

## 1. 仓库结构

```text
backend/
  app/
    main.py                 # FastAPI 入口；挂载 /api、裸 /chat 契约、启动清理
    deps.py                 # PlatformContext：会话、图、切换、权限、并发
    api/                    # chat / compat / sessions / documents / models / ...
    session/                # session.db、history、service、repository
  graph/
    main_graph.py           # LangGraph 主图
    nodes/                  # supervisor/plan/rag/execute/compress/parallel/...
    routers.py              # plan tier、RAG grade/rewrite、并行门禁
    state.py                # AgentState
    subagent.py             # 子 Agent / task 工具
    events.py               # 图内 part 事件 + turn_id
    checkpoint.py           # AsyncSqliteSaver
  harness/
    kernel.py               # TurnHarness：run/stream 生命周期
    contracts.py            # TurnContext / TurnResult / EventEnvelope
    capabilities.py         # ModelProfile / mode / TTL
    adapter.py              # 原生/回退统一输出 + 确定性 tool id
    tool_adapter.py         # complete/stream
    history.py              # 历史归一化
    delimiters.py           # 文本回退定界符
    streaming.py            # 流式解析/前置抑制
    tool_history.py         # 跨轮工具历史（锚点/上限/过期）
    context.py              # 模型视角装配（摘要 + tail + 工具历史）
    compression.py          # 压缩计划/摘要/精确值/tail
    switch.py               # SwitchService 状态机
    budget.py               # BudgetLedger
    schema.py               # ToolSchemaCache + 15 上限
    retry.py                # 可重试错误退避
    errors.py               # HarnessError / classify_error
    security.py             # 密钥脱敏
  models/                   # TierModel / LiteLLM / stream / catalog
  cognition/                # compressor / token_counter / tool_output
  execution/                # VFS / permission / tool_registry / tools
  observability/            # trace / metrics / cost
  prompts/                  # system/*.md, nodes/*.md
  tests/                    # unit / contract / integration / e2e / perf / reliability / security

frontend/
  src/
    harness/                # events / adapter / reducer / useHarnessTurn
    stores/multiAgent.ts    # 会话、消息、队列、持久化；事件交给 harness
    api/multiAgent.ts       # SSE transport
    views/MultiAgentView.vue
    mobile/                 # MobileShell + 移动页；MobileChat 复用 MultiAgentView
    types/index.ts          # 共享 TS 类型
  tests/unit|integration
```

## 2. 当前架构

### 2.1 一轮请求（后端）

```text
HTTP / SSE
  → PlatformContext.run/stream
  → TurnHarness (TurnContext: turn_id, session, model)
  → LangGraph: filter → supervisor → plan/rag/memory/compress → execute
  → AgentLoop (graph/nodes/base.py:run_tool_loop)
  → ToolAdapter (native / fallback)
  → ToolRegistry → PermissionGate → Executor
  → synthesize → memory_store → END
```

- **TurnHarness**：`harness/kernel.py`。`PlatformContext.run/stream` 已接入；`turn_id` 通过 contextvar 传播。
- **AgentLoop**：`run_tool_loop`。默认完整响应；`graph/nodes/execute.py` 传 `stream=True`，回退模型在 `ToolCallReady` 时提前执行工具。
- **模型切换**：`harness/switch.py`。门禁 → 原文备份 → 摘要/精确值探针 → 原子写 epoch → 持久化 model；失败回滚。
- **压缩**：`harness/compression.py`。切换走 `compress_messages`；图节点走 `compress_graph_messages`；手动 compact 走 `tail_snapshot`。
- **上下文视图**：`harness/context.py`。`[摘要] + [tail 原文] + [tool_history 回插] + [当前 user]`。
- **工具历史**：`harness/tool_history.py`。只保留最近一个有工具轮；最多 10 条 / 8KB / 单结果 2KB；最多注入 2 个用户轮。
- **治理**：`BudgetLedger`、`classify_error`、`retry_async`、`redact_secrets`、`ToolSchemaCache`。

### 2.2 前端（Harness 首版）

```text
MultiAgentView
  → multiAgent store (session/messages/queue/persistence/retry policy)
  → useHarnessTurn (SSE lifecycle + adapter + reducer)
  → /api/chat/multi-agent/stream
```

- `frontend/src/harness/events.ts`：`HarnessEvent` / `TurnState`。
- `frontend/src/harness/adapter.ts`：legacy 事件补 `turn_id`，缺 `seq` 保留 undefined。
- `frontend/src/harness/reducer.ts`：`turn_id + seq` 去重、`step_id` 幂等 upsert、agent/text/permission/error/terminal。
- `frontend/src/harness/useHarnessTurn.ts`：每轮 SSE + abort + reducer；store 负责队列、重试策略、IndexedDB。
- `frontend/src/components/ConversationTimeline.vue`：opencode 式单一执行时间线；从 `message.agents[].parts` 派生，不改 store 消息结构。
- `frontend/src/components/TimelineToolRow.vue`：紧凑工具行 + 展开参数/输出；`PermissionInlineCard.vue`：聊天内联审批。
- `ChatInput.vue` 仍承载附件/语音/模型/Agent/排队逻辑，仅做紧凑 Composer 样式；不要为了视觉重写语音状态机。
- **前端设计文档章节尚未补**；当前以代码与测试为准。

## 3. 开发命令

### 3.1 后端

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 启动约 30–40s；用 /api/auth/status 轮询就绪。
# 不要依赖 --reload（E: 盘热重载不可靠）；改代码后杀进程树再起。
```

```powershell
# 全量
.venv\Scripts\python.exe -m pytest

# 单元 + 契约 + 可靠性 + 安全
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q

# Harness 覆盖率门槛（.coveragerc: 90%）
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q --cov=harness

# 真实模型（默认 skip）
$env:HARNESS_SMOKE_MODEL_A='deepseek/deepseek-v4-flash'
$env:HARNESS_SMOKE_MODEL_B='deepseek/deepseek-v4-pro'
.venv\Scripts\python.exe -m pytest tests\integration\test_harness_tool_history_smoke.py tests\perf\test_harness_perf.py -q
```

### 3.2 前端

```powershell
cd frontend
npm install
npm run dev          # 5173；/api 代理到 8000

npm test             # vitest run
npm run build        # vue-tsc -b && vite build
npm run typecheck    # vue-tsc -b
```

```powershell
# live SSE 集成（需后端已启动）
$env:LIVE_API_BASE='http://127.0.0.1:8000'
npx vitest run tests/integration/
```

## 4. 契约与不变量

### 4.1 SSE 事件

- 事件字段：`type`、`turn_id`、`session_id`、`seq`，以及 payload（`agent_id`、`step_id`、`answer`、`error_type`、`error_code`、`retryable`、`status_code` 等）。
- `compat._sse` 为每个 turn 维护单调 `seq`；前端严格按 `turn_id + seq` 去重/排序。
- legacy 事件没有 `turn_id` 时，前端归入当前 turn；没有 `seq` 时按到达顺序。
- 终态：`done`（会话空闲）或 `turn_done`（还有排队轮次）；两者都必须被 store 收尾。

### 4.2 工具调用

- 统一结构：`ToolCall(id, name, arguments: str)`；Loop 不感知厂商格式。
- 确定性 tool id：`call_{seq}_{sha1(name+args)[:8]}`。
- 原生路径：`tool_calls`；回退路径：定界符解析；两者都经 `normalize_history`。
- 参数非法 → 错误作为工具结果回灌，不崩溃。
- 未知工具/权限拒绝 → 明确错误文本，不执行。
- 工具输出先过 `bound_tool_output`（头尾保留 + 落盘 `data/truncation/`），再进 `tool_history`。

### 4.3 模型切换

必须满足：

1. 切换前写原文快照；失败拒绝。
2. 目标 `context_length` 未知拒绝。
3. 会话 running 拒绝。
4. 压缩阈值/保留量与目标窗口绑定。
5. 预算累计不清零。
6. 摘要 + tail 通过精确值探针。
7. 绑定/模型/审计原子；失败回滚旧模型。
8. 消息 ID 稳定：`sessionId:seq` 或逻辑哈希，禁止 index / model+index。

### 4.4 预算与错误

- `BudgetLedger` 单调累计；`SESSION_BUDGET=0` 表示不限。
- 预算耗尽返回 `budget-exceeded` 并推 `budget_exceeded` 事件。
- **已知差异**：当前是调用后结算，不是严格“调用前预扣”；不要把它描述成预扣。
- 可重试：429/5xx/超时；不可重试：401/参数/上下文超限/预算/切换门禁。
- API/SSE 错误必须带 `retryable`、`status_code`、`error_type`、`error_code`。

## 5. 后端陷阱

- **Python**：`backend/.python-version` 为 3.13；始终用 `backend\.venv\Scripts\python.exe`。
- **启动慢**：embedding 模型、技能播种、语音预热；不要几秒没响应就判定死。
- **uvicorn --reload**：E: 盘不可靠，且可能残留幽灵 8000；改代码后 `taskkill /PID <pid> /T /F`。
- **会话存储**：模型视角历史主要来自 graph checkpoint；`session.db` 负责 UI/事件/队列/epoch。不要把两者混为一个真相源。
- **`session_inputs`**：执行中追加任务走 `/api/sessions/{id}/prompts` 队列；旧单数 `/prompt` 已删除。
- **权限**：无审批通道时 `require_approval` 工具一律拒绝；`.git` 路径永远拒绝；Shell 重定向目标要过写权限。
- **工具名**：默认 registry 注册的是 `read_file` / `write_file` / `edit_file` / `ls` / `grep` / `glob` / `append_file` / `delete_file` / `shell_exec` / `task`；插件工具名以加载器注册结果为准，不要硬编码旧前缀。
- **压缩**：`cognition/compressor.py` 是 canonical；`harness/compression.py` 是统一门面。不要再新增第三套压缩逻辑。
- **自动压缩/手动 compact**：`ContextHarness` + `tail_snapshot` 语义；手动 compact 也要写 tail 起点。
- **模型目录**：`models/catalog.py` 读取能力/窗口；`harness/capabilities.build_profile` 有 60s TTL，人工 override 优先。
- **上下文窗口**：未知窗口时 `resolve_mode` 保守回退；不要在未知窗口上做原生工具假设。
- **不要重新引入**：`MAX_STEPS`、`SUB_AGENT_TIMEOUT`、`SUPERVISOR_TIMEOUT`、`AGENT_TIMEOUT_MULTIPLIERS`、`DOOM_LOOP_*`；这些字段/逻辑已删除。
- **不要引用**：`backend/scripts/`（整目录已删除）、旧 `backend/app/agent/**`、`app/context/**`、`scripts/compact_session.py`。

## 6. 前端陷阱

- **store 公共 API 不变**：`send`/`cancel`/`loadConversation`/`undoMessage` 等签名保持；内部事件处理走 `useHarnessTurn`。
- **不要重置消息列表**：`model_switched` 只更新后续 turn 的模型；消息 key 用稳定 id。
- **持久化**：`text_delta` 不写 IndexedDB；queued/done/turn_done/error 等阶段才写。服务端是唯一真相源，缓存只做离线回退。
- **权限**：`permission_request` 归一化后交给 `PermissionStore`；不要塞回 reducer 里弹窗。
- **排队**：`turn_done` 由 store 决定 drain/下一轮；controller 只产出终态。
- **移动端**：`MobileChat` 复用 `MultiAgentView`；改聊天逻辑时先确认移动端不被破坏。
- **聊天表面**：主结构是 `ConversationTimeline` 的单一时间线；不要重新引入按 Agent 分块的 `MultiAgentResponse` 作为主渲染路径。
- **只展示真实工具**：时间线过滤 `!step.tool_name` 的 Harness 图内节点（filter/plan/rag/execute_subagent 等）；不要渲染 `NODE_LABELS` 或 Agent 过滤 chips。
- **工具步骤**：默认紧凑行，点击展开；不要默认展开所有参数/输出，避免长任务淹没最终答案。
- **审批**：聊天页用 `PermissionInlineCard`，`Sidebar` 中的模态在 `/multi-agent` 路由隐藏；不要两套同时弹。
- **旧组件**：`MultiAgentResponse.vue` / `AgentPanel.vue` 仍保留给旧测试与回滚，但视图不再引用；验收通过前不要删。
- **构建**：没有独立 lint script；`npm run build` 内含 `vue-tsc -b`。

## 7. 新增/修改时的落点

- **新 API**：`backend/app/api/` 加 router → `app/main.py` include；前端 `frontend/src/api/` 加 client → `errors.ts` 统一解包。
- **新工具**：`execution/tool_registry.py` 注册；函数名 `tool_*`（插件）或 `register_function`；更新工具描述；权限/沙箱/输出边界要一起考虑。
- **新 Skill**：`backend/skills/` Markdown + frontmatter；需要时更新 `skills` 目录 UI/loader。
- **新 Plugin**：`backend/plugins/*.py`，`tool_*` 自动注册；`.enabled` 文件控制启用。
- **新前端事件**：先加 `harness/events.ts` 类型 + `adapter`/`reducer` 分支 + 单测，再接 store/view。
- **新提示词**：`prompts/system/*.md`、`prompts/nodes/*.md`；不要把大段规则塞进 Python。

## 8. 验收与已知缺口

当前实测（2026-10-24，本地 Windows）：

- 后端：`514 passed, 16 skipped`
- Harness 覆盖率：`90.80%`（门槛 90%）
- 前端单测：`104 passed`
- 前端 live SSE 集成：`119 passed`
- 前端 `npm run build`：通过
- 真实模型：DeepSeek `deepseek/deepseek-v4-flash → deepseek-v4-pro`；E2E 8 条、PF-01/02/05/06/07 通过

已知缺口：

- **D-01**：预算事件已补，但计费仍是调用后结算，不是严格预扣。
- **前端 Harness 设计文档章节**尚未补；代码/测试已落地。
- 真实模型/性能测试默认 env-gated；缺 `HARNESS_SMOKE_MODEL_A/B` 时记为 blocked，不要伪报通过。
- `backend/README.md`、`frontend/README.md` 未随本文件一起重写（本轮只更新根文档）。

## 9. 文档索引

- `docs/harness-architecture-design.md`：Harness v2 设计（权威）
- `docs/harness-design-gap-matrix.md`：G-01~G-16、§13 验收、阶段记录
- `docs/agent.md`、`agent-1.md`~`agent-4.md`：历史设计依据
- `docs/codeact-sandbox-design.md`：CodeAct
- `docs/移动端聊天交互设计要点-ChatGPT范式.md`：移动端聊天
- `README.md`：人类入口
