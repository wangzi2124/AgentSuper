# Knowledge Base System — RAG + Agent Harness

基于 RAG 的知识库问答与多 Agent 执行系统。后端用 LangGraph 编排检索、规划、工具执行与上下文压缩；前端用 Vue 3 + Harness 事件层渲染多 Agent 流式过程。

> 人类入口：本文件。Coding agent 入口：[`AGENTS.md`](AGENTS.md)。
> 架构权威：[`docs/harness-architecture-design.md`](docs/harness-architecture-design.md)。验收/差距：[`docs/harness-design-gap-matrix.md`](docs/harness-design-gap-matrix.md)。

---

## 为什么是 Harness

不同模型对工具调用的支持差异很大：有的原生返回 `tool_calls`，有的只能输出文本。Harness 把差异收在一层：

- **协议适配**：原生 `tool_calls` 与文本定界符两条路径统一成 `ToolCall(id, name, arguments)`。
- **上下文连续**：模型切换时备份原文、压缩摘要、回填精确值、原子写绑定；失败回滚。
- **可控成本**：压缩门禁、扇出门禁、`BudgetLedger`、20 个工具上限。
- **可恢复**：确定性 tool id、`turn_id + seq` 事件、checkpoint 恢复、错误分类与重试。
- **可测**：真实模型 E2E / 性能项与假模型回归分离，默认 CI 不烧 token。

---

## 核心能力

| 能力 | 说明 |
| --- | --- |
| **Agent Harness** | 原生/文本双路径工具调用、历史归一化、流式执行、确定性 tool id |
| **快速寒暄/介绍** | “你好/谢谢/简单介绍/你是谁/你能做什么”走固定模板，不调用模型 |
| **Ollama 兼容** | 原生 tools 与无 tools 文本回退双路径；本地模型默认不设为默认模型 |
| **RAG 混合检索** | 向量 + BM25 + RRF；章节感知分块、父子块展开、grade/rewrite 质量门禁 |
| **多 Agent 编排** | filter → supervisor → plan/RAG/memory/compress → execute → synthesize；串行与并行扇出、后台回合 |
| **跨模型切换** | 原文快照、摘要迁移、精确值探针、原子提交/回滚、审计 |
| **上下文压缩** | 专用压缩 Agent 摘要 + tail 保护；不再物理删除历史，前端折叠压缩卡；自动/手动/切换共用一套 |
| **工具与权限** | 注册表、scope、20 上限、有界并发（读写锁）、审批、沙箱、Shell 重定向检查、输出有界化 |
| **成本治理** | BudgetLedger、压缩/扇出门禁、token/cost 统计、预算耗尽事件 |
| **可靠性** | 429/5xx 退避、fallback、取消、断连清理、checkpoint 恢复 |
| **安全** | 密钥脱敏、不可信工具内容隔离、路径/命令/SSRF 防护 |
| **Web Chat v2** | durable inbox、全局事件总线（`GET /api/v2/event` SSE）、断线续传、fork/revert/compact、快照持久化 |
| **模型管理** | Provider/模型目录 CRUD；Provider 扩展字段（timeout/headers/body/package/compatibility）保存即参与运行；Ollama 窗口守卫与自动扩窗、`#variant` 变体、LM Studio/vLLM 发现 |
| **MCP** | stdio/HTTP JSON-RPC 真实客户端；OAuth 2.1（PKCE/动态注册）；prompts/resources 可浏览调用 |
| **扩展能力** | 指令（AGENTS.md 发现）、斜杠命令（`$ARGUMENTS`/`@file`/subtask）、Markdown Agents、策略引擎、引用、自定义工具、格式化器 |
| **Skills / Plugins** | Markdown 技能与 Python `tool_*` 插件动态扩展 |
| **前端 Harness** | `frontend/src/harness/`：事件契约、legacy adapter、turn reducer、SSE composable |
| **聊天时间线** | opencode 式单一执行时间线；只展示真实工具调用与答案，隐藏图内节点；内联审批/错误、紧凑 Composer |
| **会话与桌面** | 会话树/fork/revert/compact、排队输入、工作目录分组；桌面标签页外壳，移动端复用聊天视图 |

---

## 架构简图

```mermaid
flowchart LR
  U[用户 / 前端] --> API[FastAPI / SSE]
  API --> TH[TurnHarness]
  TH --> G[LangGraph 主图]
  G --> L[AgentLoop]
  L --> TA[ToolAdapter<br/>native / fallback]
  TA --> M[Model Runtime<br/>TierModel / ModelRuntime / LiteLLM]
  L --> R[ToolRegistry<br/>Permission / Executor]
  TH --> C[Compression<br/>summary / tail]
  C --> S[(checkpoint)]
  C --> DB[(session.db)]
  TH --> B[BudgetLedger]
  TH --> E[EventBus<br/>turn_id / seq]
  E --> API
```

前端：

```text
V2ChatView（桌面与移动端共用）
  → v2/sessionStore（session runtime + 幂等事件 reducer）
  → v2/eventStream（单连接共享 SSE）
  → /api/v2/session/* + GET /api/v2/event
```

---

## 快速启动

### 前置

- Python **3.13**（见 `backend/.python-version`）
- Node.js 18+
- 可选：`uv`（更快安装）

### 后端

```powershell
cd backend
# 首次
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 配置
copy config.example.json config.json
# 编辑 config.json，至少填写 llm_api_key / llm_api_base / llm_model

# 启动（约 30–40s；不要用 --reload）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

就绪检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/auth/status
```

### 前端

```powershell
cd frontend
npm install
npm run dev
# http://localhost:5173；/api 代理到 127.0.0.1:8000
```

生产构建：

```powershell
npm run build
npm run preview
```

---

## 测试与验收

### 后端

```powershell
cd backend
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q --cov=harness
```

### 前端

```powershell
cd frontend
npm test
npm run build
# 先退出 Ollama（托盘退出），设置用户环境变量，再重启 Ollama：
setx OLLAMA_CONTEXT_LENGTH 4096
setx OLLAMA_NUM_PARALLEL 1
setx OLLAMA_MAX_LOADED_MODELS 1
setx OLLAMA_FLASH_ATTENTION 1
setx OLLAMA_KV_CACHE_TYPE q8_0

# 需后端已启动
$env:LIVE_API_BASE='http://127.0.0.1:8000'
npx vitest run tests/integration/
```

### 真实模型（可选，默认 skip）

```powershell
$env:HARNESS_SMOKE_MODEL_A='deepseek/deepseek-v4-flash'
$env:HARNESS_SMOKE_MODEL_B='deepseek/deepseek-v4-pro'
cd backend
.venv\Scripts\python.exe -m pytest tests\integration\test_harness_tool_history_smoke.py tests\perf\test_harness_perf.py -q
```

当前实测（2026-11-10，本地 Windows，Provider 表单修复轮）：

- 后端：`tests\unit tests\contract` **837 passed** / 0 failed；全量四套件（unit/contract/reliability/security）上次 2026-11-03 为 **846 passed**、Harness 覆盖率 **92.46%**（门槛 90%）
- 前端单测：`89 passed | 118 skipped`（`npm test`；live 用例需后端）
- 前端 `npm run build`：通过（内含 `vue-tsc -b`）
- 前端 live SSE 集成：上次 `119 passed`（需后端启动）
- 真实模型：DeepSeek `deepseek-v4-flash → deepseek-v4-pro`；E2E 8 条、PF-01/02/05/06/07 通过（env-gated，默认 skip）

---

## 配置重点

| 配置项 | 说明 |
| --- | --- |
| `llm_api_key` / `llm_api_base` / `llm_model` | 模型凭证与默认模型 |
| `session_budget` | 会话预算；`0` 表示不限 |
| `max_concurrent_agents` | 并发 Agent 上限 |
| `compaction_auto` / `compaction_buffer` / `compaction_tail_turns` | 自动压缩开关、窗口缓冲（默认 15%）、尾部保护的工具轮数（默认 2） |
| `compaction_keep_tokens` / `compaction_min_gain_tokens` / `compaction_prune*` | 保留预算 / 最小收益门禁 / 确定性修剪 |
| `compress_model` / `compress_model_enabled` / `compress_max_tokens` | 压缩 Agent 专用模型 |
| `tool_concurrency` | 同轮工具调用并发上限（默认 10；写工具独占写锁，读工具共享读锁） |
| `plan_parallel_enabled` | 并行扇出开关（默认关闭） |
| `harness_tool_mode` | `native` / `fallback`，排障强制模式 |
| `harness_smoke_model_a/b` | 真实模型验收模型对 |

配置来源：`backend/config.example.json`（全部参数与默认值）→ 复制为 `backend/config.json`。
优先级：操作系统环境变量 > `config.json` > 代码默认值；`.env` 文件不再读取。

数据根：`agentsuper_data` 留空 = 平台用户目录（Windows `%LOCALAPPDATA%\agentsuper`，macOS `~/Library/Application Support/agentsuper`，Linux `~/.local/share/agentsuper`）；可改为 `D:/agentsuper` 等绝对路径。首次启动会把旧的 `backend/data` 自动拷贝到空数据根（`agentsuper_migrate_legacy=false` 可关闭）。

---

## 目录

```text
backend/      FastAPI + LangGraph + Harness + RAG + 执行/权限
frontend/     Vue 3 + Pinia + Harness 事件层 + 移动端
docs/         设计、差距矩阵、历史方案
deploy/       Nginx/Caddy 部署配置
data root/    运行时数据（会话、检查点、截断输出、模型目录；默认平台用户目录，可用 agentsuper_data 覆盖）
```

---

## 已知边界

- 预算计费目前是**调用后结算**，不是严格调用前预扣（D-01）。
- 真实模型/性能测试默认跳过，需要显式设置模型对与凭证。
- 前端 Harness 设计文档章节尚未补；当前以 `frontend/src/harness/`、`frontend/src/v2/` 与测试为准。
- 旧聊天组件（MultiAgentView / ConversationTimeline / MultiAgentResponse / AgentPanel / ChatInput）已在 v2 成为默认路由后删除；聊天逻辑改动需同时确认桌面与移动端。
- MCP OAuth 尚未实现 scope 升级（403 `insufficient_scope`）与 Client ID Metadata Documents（CIMD）。
- `backend/README.md`、`frontend/README.md` 仍可能落后于根文档。

---

## 文档索引

- [`AGENTS.md`](AGENTS.md)：coding agent 工作手册
- [`docs/harness-architecture-design.md`](docs/harness-architecture-design.md)：Harness v2 设计
- [`docs/web-chat-v2-refactor-design.md`](docs/web-chat-v2-refactor-design.md)：Web Chat v2（durable inbox / message-part / global event）全量重构设计
- [`docs/harness-design-gap-matrix.md`](docs/harness-design-gap-matrix.md)：G-01~G-16 与验收
- [`docs/agent.md`](docs/agent.md)：总体 Agent/RAG 设计
- [`docs/agent-1.md`](docs/agent-1.md) ~ [`agent-4.md`](docs/agent-4.md)：压缩/检索/切换/工具协议
- [`docs/codeact-sandbox-design.md`](docs/codeact-sandbox-design.md)：CodeAct
- [`docs/移动端聊天交互设计要点-ChatGPT范式.md`](docs/移动端聊天交互设计要点-ChatGPT范式.md)：移动端聊天
