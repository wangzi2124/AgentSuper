# OpenCode V2 能力对照：AgentSuper 代码实现报告

> 对照文档：`桌面/OpenCode-技术与实现文档.md`（整理自 opencode.ai/docs V2，2026-11-09）
> 代码基线：`C:\tool\AgentSuper` 当前工作区（2026-11-09）
> 审计方式：静态代码审计（逐模块读代码 + grep 交叉验证）+ 目标单测抽查
> 抽查结果：15 个测试文件、86 个用例全部通过（命令见文末）
> 说明：本报告只描述代码事实；「文档声称」与「代码实际」不一致处单独标注。

---

## 0. 结论摘要

### 0.1 一句话结论

AgentSuper **不是 OpenCode 的完整复刻**，而是「OpenCode 的 Web/Agent 核心能力 + 自有 RAG/知识库/MCP/模型管理」的本地化实现。

- **已对齐的核心**：会话与 durable inbox、TurnHarness/AgentLoop、工具注册与权限、压缩、快照回滚、Agent 定义、指令、命令、技能、格式化、MCP（stdio/HTTP）、References、Policies、Providers/Variants、本地模型发现、v2 事件流、OpenAI 兼容 API。
- **明确未做/不属于本项目**：CLI/TUI、Desktop 原生壳、ACP、Client SDK/嵌入式 SDK、Console/组织托管配置、JS/TS 插件体系、浏览器工具、Code Mode `execute`、worktree 多工作树、Provider 原生压缩/后台压缩。
- **主要语义缺口**：配置只有单层 + 环境变量（无 opencode.json 分层/`{env:}`/`{file:}`）、无 Location 懒加载与空闲卸载、无多 worktree、MCP 无 OAuth/Code Mode/prompt 命令/pool/协议协商、插件只有 Python 无 Transform/RPC 体系、事件流无 `Last-Event-ID` 回放（靠 REST hydration）、无原生/后台压缩、无共享托管策略。

### 0.2 总览

图例：✅ 已实现　🟡 部分实现　❌ 缺失　⛔ 明确不做/不适用

| 文档章节 | 能力 | 状态 | 主要落点 |
|---|---|---|---|
| §1 概述 | 编码 Agent 三形态（CLI/Desktop/Web） | 🟡 | 仅 Web（FastAPI + Vue） |
| §2 架构 | 客户端-服务器、共享状态 | ✅ | `app/main.py`、`app/deps.py`、`session.db` |
| §2 Location/Project | Location 作用域、懒加载、空闲卸载 | 🟡 | 仅 project 检测，无多 worktree/懒加载 |
| §3 安装部署 | CLI 安装、pair/QR、service 管理 | ⛔/🟡 | 有 Docker/K8s/Caddy/nginx；无 CLI/pair/service |
| §4 配置 | opencode.json 分层、变量替换、实验项 | 🟡 | 单 `config.json` + env + 默认值 |
| §5 会话 | inbox、steer/queue、fork/move/export/import | 🟡 | inbox/fork/archive 有；move/export/import 无 |
| §5 Worktree | 多 worktree 创建/列表/删除 | ❌ | 仅 git worktree 识别，无管理 |
| §6 Provider/模型 | Package、variants、compatibility、传输 | 🟡 | LiteLLM + variants + headers/body；无包体系/WS/zstd |
| §7 Agent | 主/子 Agent、Markdown/JSONC、steps/request | 🟡 | Markdown 注册 + 内置角色；无 JSONC/request 运行时 |
| §8 工具 | read/glob/grep/edit/write/patch/shell/web/question | ✅/🟡 | 文件/shell/web/question/patch 有；无 execute/browser/image |
| §8 权限扫描器 | Bash/Zsh/sh/PowerShell 语义扫描 | 🟡 | 自有保守拆分器（非完整语法扫描） |
| §9 MCP | stdio/HTTP、资源、prompts、OAuth、pool | 🟡 | 真实 stdio/HTTP + tools/resources/instructions；OAuth 501 |
| §10 权限/策略 | per-agent 规则、审批、硬策略 | 🟡 | 两套均有；审批语义与外部目录默认值有差异 |
| §11 上下文 | 压缩、原生/后台压缩、快照、附件、引用 | 🟡 | 自动/手动压缩、快照、附件、References 有；原生/后台压缩无 |
| §12 指令/技能/命令 | AGENTS.md、skills、commands | ✅/🟡 | 指令完整；技能缺支持文件；命令缺 shell/subagent/template |
| §13 插件 | Server/TUI/GUI/RPC、transform、hooks | 🟡/⛔ | Python 插件 + hooks；JS/TS/RPC/TUI/GUI 明确不做 |
| §14 API/SDK | 170+ API、Client SDK、嵌入式 SDK、ACP | 🟡/⛔ | v2 API 子集 + OpenAI 兼容；SDK/ACP 无 |
| §15 CLI | run/session/auth/mcp/plugin/serve/pair/debug | ⛔ | 不适用（Web 产品） |
| §16 运维 | 服务管理、日志、堆快照、代理 | 🟡 | 日志/代理有；服务管理/堆快照无 |
| §17 设计模式 | Location、Transform、分层权限、预算 | 🟡 | 部分对齐（权限流水线、预算较接近） |

---

## 1. 系统架构与部署（对应 §2、§3）

### 1.1 已实现

- **客户端-服务器结构**：FastAPI 服务端 + Vue Web 客户端，会话/配置/权限/工具执行都在服务端。
  - 入口：`backend/app/main.py:411-418`
  - 平台上下文：`backend/app/deps.py`（会话、图、切换、权限、并发）
- **会话真相源**：`session.db` 的 `session_messages` + `message_parts`；每轮由 projector 投影，checkpoint 只做单轮运行时。
  - `backend/app/session/repository.py`、`backend/app/session/projector.py`
- **多客户端共享**：Web 桌面外壳 + 移动端复用同一 `V2ChatView`（`frontend/src/views/V2ChatView.vue`）。
- **部署**：`backend/deploy/Dockerfile`、`backend/deploy/docker-compose.yml`、`backend/deploy/nginx.conf`、`backend/deploy/Caddyfile`、`backend/deploy/k8s/`。

### 1.2 差距

- **没有 Location 懒加载与 `location_idle_timeout`**。每个会话直接持有目录；项目检测在 `backend/app/filesystem/project.py`，但没有按 Location 懒加载/卸载服务。
- **没有 service 注册/发现**（`service.json`）、`opencode serve/pair`、一次性配对码、`OPENCODE_PASSWORD` / HTTP Basic、30 天会话 cookie、OpenTunnel 远程访问。
- **没有 CLI/TUI/Desktop/ACP**。项目是 Web 产品，此项按文档第 14/15 章属于非目标。
- **没有共享服务器 + `--standalone` + `--server <url>` 三种启动模式**；只有 uvicorn 启动。
- **有一点比 OpenCode 文档更严格**：AgentSuper 的事件是 durable outbox（`repository.append_event`），而 OpenCode 文档写的是「实时、不重放」。但 AgentSuper 的 SSE 本身也没有 `Last-Event-ID` 回放，重连后靠前端 `hydrate` 走 REST 重新拉取，所以语义上仍是「实时 + 客户端 resync」。

---

## 2. 配置系统（对应 §4）

### 2.1 已实现

- **JSONC 支持**：`backend/config.py:21-78`（注释、尾逗号）。
- **优先级**：环境变量 > `backend/config.json` > `config.py` 默认值；`.env` 不再读取。
  - `backend/config.py:1-12`
- **配置 API/UI**：`GET/PATCH /api/v2/config`、`POST /api/v2/config/rollback`，配置页在 `frontend/src/views/AgentSettingsView.vue`。
  - `backend/app/api/v2/config.py:97-165`
- **已覆盖的配置域**：
  - 模型/Provider、权限 `permissions`、硬策略 `policies`、指令、formatter、压缩、warming、websearch、图片/附件、`tool_output_max_tokens`、MCP（独立 `mcp.json`）、`plugins_dir`。
  - `backend/config.py:200-407`

### 2.2 差距

- **没有 OpenCode 的四层配置合并**（系统/托管/全局/项目 + 从当前目录向上搜索 + `.opencode` 覆盖同目录直接配置）。本仓库只有单个 `backend/config.json`。
- **没有 `{env:NAME}` / `{file:path}` 字符串变量替换**；环境变量走 pydantic-settings，但配置文件里不能写 `{file:...}`。
- **没有 `$schema` 编辑器校验入口**（`config.example.json` 是完整字段样例，但不是 opencode schema URL 语义）。
- **没有 `cli.json`**（纯 Web，无终端客户端配置）。
- **没有 `experimental` 配置块**（todo/subagent_depth/background_compaction/image_generate/location_idle_timeout）。
- **没有 `update`、`watcher.ignore`、`snapshots: false`、`worktree.directory`、`references`/`commands` 的内联 JSONC 配置**：
  - References 走 `~/.config/agentsuper/references.json`
  - Commands 走目录 Markdown
  - Agents 走 Markdown
  - MCP 走 `mcp.json`
- **没有 `tool_output.max_lines/max_bytes`**：本项目用 `tool_output_max_tokens`（`backend/config.py:244`），口径不同。
- **配置项完整性测试存在**：`backend/tests/unit/test_config_example.py`（新增配置项必须同步）。

---

## 3. 会话、Location 与运行时（对应 §5）

### 3.1 已实现

- **会话模型**：目录/项目字段、`parent_id`、标题、agent、model、归档。
  - 创建与列表：`backend/app/api/v2/session.py:371-419`
  - 子会话过滤：`:397-418`
  - 归档事件：`:439-453`
- **Durable inbox + steer/queue**：
  - 契约：`backend/app/contracts/v2.py:13`（`Delivery = Literal["steer","queue"]`）、`:103-112`
  - 入队/更新/删除：`backend/app/api/v2/session.py:483-597`
  - 串行 drain：`backend/app/session/coordinator.py`
  - 事件：`session.inbox.enqueued/delivered/cancelled/delivery.changed`
- **Agent Loop 主链路**：`PlatformContext.run/stream → TurnHarness → LangGraph → run_tool_loop → ToolAdapter → ToolRegistry/PermissionGate`。
  - `backend/graph/nodes/base.py`、`backend/harness/kernel.py`、`backend/harness/tool_adapter.py`
- **Fork / archive / revert / restore**：
  - fork：`backend/app/api/v2/session.py:750-770`
  - revert stage/commit/clear：`:663-708`
  - diff：`:711-723`
  - restore：`:726-747`
- **步骤上限**：`max_rounds` 存在于子 Agent（`backend/graph/subagent.py:41`）和 `run_tool_loop`（`backend/graph/nodes/base.py:736-995`），但主循环的 agent frontmatter `steps` 未接线（见 §5 Agent）。

### 3.2 差距

- **没有 `POST /api/v2/session/{id}/move`**、export/import、`/session/active`、`/experimental/session/{id}/wait`、`/session/{id}/shell`。
  - 已注册路由清单：`backend/app/api/v2/session.py`（25 个路由）
- **没有 `parentID` 之外的「子会话可再启动子 Agent」深度控制**（OpenCode `subagent_depth`）。
- **没有 `steer` 在「下一步边界」插入的完整调度语义核验**：有 delivery 字段和 inbox 事件，但未见与模型 step 边界的专门约束测试。
- **会话 move 只在契约/inbox 类型里出现**（`InboxMoveItem`，`contracts/v2.py:352-355`），未见对应 REST 路由。
- **Worktree 管理缺失**：
  - 仅有「识别 git worktree / 把仓库根当工作区」：`backend/app/filesystem/project.py`、`backend/execution/permission.py:291-313`
  - 没有 `worktree_create/list/remove` 工具或 API，没有 `worktree.directory` 配置，没有多 worktree 的 project 抽象。

---

## 4. Provider 与模型层（对应 §6）

### 4.1 已实现

- **模型目录 + 本地发现**：`backend/models/catalog.py`、`backend/models/builtin_catalog.py`、`backend/models/catalog_db.py`、`backend/models/local_discovery.py`。
  - LM Studio（默认 1234）/ vLLM（默认 8000）探测：`local_discovery.py:9-78`
  - Ollama 适配：`backend/models/runtime.py`、`backend/models/context_guard.py`
- **`provider/model#variant` 解析与合并**：
  - `backend/models/variants.py`（`split_variant`、`find_variant`、`apply_variant`）
  - Provider/模型级 `headers` / `body` / `settings` 合并
  - 测试：`backend/tests/unit/test_model_variants.py`
- **Provider 级字段**：`label/api_base/api_key/enabled/type/package/compatibility/timeout/headers/body`。
  - `backend/models/catalog.py:720-792`
- **提示缓存**：
  - Anthropic `cache_control`：`backend/models/cache_hints.py`
  - OpenAI/Azure/OpenRouter/OpenCode/Venice 的 `prompt_cache_key` + `x-session-affinity`：`backend/models/runtime.py:472-505`
- **DeepSeek 图片兼容**：含图请求走 `openai/<model>` + DeepSeek base，避免 LiteLLM `deepseek/` 丢 `image_url`。
  - `backend/models/runtime.py:452-471`
- **Ollama 窗口守卫/自动扩窗/overflow 恢复**：`backend/models/context_guard.py`、`backend/models/runtime.py`。
- **模型切换状态机**：`backend/harness/switch.py`；门禁、摘要/精确值探针、原子写、失败回滚。

### 4.2 差距

- **没有 OpenCode 的运行时包体系**（`@opencode/ai/providers/*`、npm 包、`file://` 本地包）。本项目是 LiteLLM adapter + 目录配置。
- **没有 WebSocket 传输**（OpenCode 对 OpenAI/xAI/Azure Responses 的连续步骤复用）。
- **没有 `settings.encoding`（zstd/gzip/none）压缩请求体**。
- **没有 `headerTimeout` / `chunkTimeout` / 整请求 `timeout` 的完整三档控制**；Provider 只有单一 `timeout` 秒数。
- **没有 `compatibility` 的完整字段语义**：`reasoningField`、`requireReasoning`、`maxTokensField`、`requireFinishReason`、`requireAssistantAfterTool`、`supportsPromptCacheKey`、`streamUsage`、`cacheFormat`、`supportsThinkingBlockBinding`、`supportsEffortUpdates`、`mediaInput`。
  - 代码里 `compatibility` 只作为 Provider/模型字段保存并透传（`models/catalog.py:747,784`），未见逐字段解释到请求。
- **没有 `models.dev` 全量目录**；目录来自内置 + provider 探测 + 本地发现 + 人工添加。
- **没有 `disabled` 之外完整的模型类型（image/decision）语义**；主要按 chat/vision 使用。
- **没有 `experimental_image_generate` 工具**。

---

## 5. Agent 系统（对应 §7）

### 5.1 已实现

- **Agent 注册表**：内置 `backend/agents/*.md` → 全局 `~/.config/agentsuper/agents/*.md` → 项目 `<ws>/.agentsuper/agents/*.md`。
  - `backend/agents/registry.py:1-7`
- **字段**：`name/description/mode/tier/model/tools/permission/request/max_rounds/hidden/disabled/color/source/path/prompt_body`。
  - `backend/agents/registry.py:26-43`
- **嵌套 ID**：registry 支持嵌套路径（`registry.py:118-202`）。
- **内置 Agent**：`build/plan/general/explore/compaction/title/summary`，另有 `research/code/file`；`title/summary` hidden+disabled。
  - `backend/agents/*.md`
- **回退 build**：`backend/agents/resolver.py:7-27`
- **运行时应用**：execute 节点消费 agent 的 system/model/tools/permission。
  - `backend/graph/nodes/execute.py:193-304`
- **权限合并**：`backend/agents/registry.py:118-168`，agent permission 进入 AccessController（`execution/access.py`）。
- **API**：`/api/v2/agents` CRUD（`backend/app/api/v2/agents.py`）+ 会话切换 agent（`session.py:796-807`）。
- **子 Agent**：`backend/graph/subagent.py`，`max_rounds=6`，通过 `task` 工具启动。

### 5.2 差距

- **没有 JSONC `agents{}` 配置对象**；只能 Markdown 文件。
- **`request` 只解析/合并/序列化，运行时未应用**（`agents/registry.py:156-157`、`app/api/v2/agents.py:30`）。
- **`steps`（主 Agent 最大模型步数）未接到主 `run_tool_loop`**；只有子 Agent `max_rounds`。
- **`mode=primary/subagent/all` 的完整发现约束**：代码主要区分 primary/subagent，`all` 行为与 OpenCode 的「两处皆可」未见专门测试。
- **`hidden` 只是可见性**（与 OpenCode 一致，非安全机制）。
- **没有 `disabled` 在内置 Agent 配置点的移除语义测试**。
- **`task` 工具描述基于静态 `SUBAGENTS`**（`graph/main_graph.py:173`），项目新增 Agent 不一定出现在描述里。

---

## 6. 工具系统与执行模型（对应 §8）

### 6.1 已实现

内置工具（`backend/graph/main_graph.py:91` 注册、`backend/execution/tools/*`）：

| 工具 | 状态 | 落点 |
|---|---|---|
| `read_file` / `ls` / `glob` / `grep` | ✅ | `execution/tools/file_tools.py` |
| `write_file` / `edit_file` / `append_file` / `delete_file` / `create_directory` | ✅ | `execution/tools/file_tools.py` |
| `apply_patch`（unified diff） | ✅ | `execution/tools/patch_tools.py:67` |
| `shell_exec` | ✅ | `execution/sandbox.py:27` |
| `web_search` / `web_fetch` / `http_request` / `get_weather` | ✅ | `execution/tools/web_tools.py`、`weather_tools.py` |
| `ask_question` | ✅ | `execution/tools/question_tools.py:14` |
| `task`（子 Agent） | ✅ | `graph/subagent.py` |
| MCP 工具 | ✅ | `execution/tools/mcp_client.py`、`mcp/runtime.py` |

- **工具并发/写锁**：同一轮工具按 `TOOL_CONCURRENCY` 有界并发；写与 shell 串行（`graph/nodes/base.py:814`、`execution/actions.py`）。
- **结果边界**：`bound_tool_output` + `ToolOutputPruner`（`cognition/tool_output.py`、`cognition/compressor.py`），默认 token 上限 `tool_output_max_tokens=8000`。
- **格式化**：`write_file`/`edit_file` 后触发 `execution/formatters/`；`shell=False`、超时、首个成功即停。
  - `backend/execution/tools/file_tools.py:35-54`、`backend/execution/formatters/service.py:37-143`
- **MCP 资源**：`mcp/runtime.py` 暴露 `resources` 与 `read_resource`，API 有 `/servers/{name}/resources/read`。
- **Code Mode `execute`**：未实现；ADR-0002 明确不做 browser/execute 工具（`docs/adr/0002-browser-execute-tools.md`）。

### 6.2 差距

- **无 `execute`（Code Mode JavaScript）**，也无命名空间 `opencode.*` / `browser.*`。
- **无浏览器工具**（tab/导航/点击/截图/控制台/网络/性能/Lighthouse）。
- **无 `experimental_image_generate`**。
- **无 `list_mcp_resources` / `read_mcp_resource` 模型可调用工具**；只有 API 级资源读取和 runtime 方法。
- **无 `patch` 仅对部分 GPT 模型暴露的门控**；本项目 `apply_patch` 是通用工具。
- **格式化覆盖不完整**：`append_file` 直接 `vfs.write_file` 不触发 `_after_mutation`；`apply_patch` 也无 formatter hook。
  - `backend/execution/tools/file_tools.py:84-90`
- **Shell 扫描器不是完整语言级扫描**：用正则拆 `|| && | ; \n`，遇 `$(...)`/反引号/重定向强制人工审批（`execution/permission.py:110-157`）。这是保守安全差异，不追求 Bash/Zsh/PowerShell AST 级解析。
- **工具输出限制口径不同**：OpenCode 是 `max_lines=2000 / max_bytes=51200`；本项目是 token 上限 + 头部/尾部保留 + 落盘。

---

## 7. MCP 集成（对应 §9）

### 7.1 已实现

- **真实 transport**：
  - stdio：`backend/mcp/client.py:29+`
  - Streamable HTTP：同文件 HTTP transport
  - 失败显式报错，无内存 stub：`backend/mcp/runtime.py:1-10`
- **配置**：`~/.config/agentsuper/mcp.json`、`<ws>/.agentsuper/mcp.json`；兼容 `servers` 与 `mcpServers`。
  - `backend/mcp/config.py:1-120`
- **工具注册**：`mcp_<server>_<tool>`，非法字符替换 `_`；冲突跳过并告警。
  - `backend/mcp/runtime.py:30-33`、`:130-170`
  - 注意：OpenCode 是 `<server>_<tool>`，本仓库前缀是 `mcp_`。
- **资源**：`resources/list`、`resources/read`（`runtime.py:190-210`）。
- **服务器 instructions**：收集并把 `has_instructions` 暴露给 UI；`instructions()` 汇总。
  - `backend/mcp/runtime.py:213-230`
- **API**：`GET /api/v2/mcp`、CRUD `/servers`、`/reload`、`/servers/{name}/tools|resources|resources/read|instructions|auth`、`/{name}/connect|disconnect`。
  - `backend/app/api/v2/mcp.py:47-170`
- **UI**：`frontend/src/views/McpSettingsView.vue`。
- **真实 stdio 集成测试**：`backend/tests/integration/test_mcp_stdio_real.py`。
- **单元测试**：`backend/tests/unit/test_mcp_runtime.py`。

### 7.2 差距

- **OAuth 未实现**：`POST /servers/{name}/auth` 直接 501，提示手配 headers（`api/v2/mcp.py:143-149`）。无 PKCE/DCR/CIMD/token 刷新。
- **无 Code Mode 暴露**：MCP 工具直接进原生工具表；无 `codemode: false` 概念，也没有按 server 分组命名空间。
- **无 MCP prompts → 命令**（`/context7:find_docs`）。
- **无协议版本协商**：`PROTOCOL_VERSION = "2024-11-05"`（`client.py:24`），没有 `legacy/auto/2026-07-28` 与 `server/discover`。
- **无连接池 `pool` 与跨项目共享连接**。
- **无细粒度超时**（startup/catalog/execution 三档）；只有单个 `timeout`（默认 20s）。
- **配置字段不全**：无 `cwd`、`codemode`、`protocol`、`pool`、`oauth`；有 `command/args/env/url/headers/enabled/timeout`。
- **服务器 instructions 的权限裁剪**（拒绝该 server 全部工具时省略）未实现。
- **`CallToolRequest.params._meta["ai.opencode/sessionID"]`** 未实现。

---

## 8. 权限与策略安全模型（对应 §10）

### 8.1 已实现

- **Permissions（per-agent 工具规则）**：
  - `PermissionManager`：`backend/execution/permission.py`
  - 动作模型：`backend/execution/actions.py`（read/edit/glob/grep/shell/subagent/skill/question/webfetch/websearch/external_directory/mcp_tool）
  - 规则 effect：allow/deny/ask；最后匹配生效；路径规范化（`permission.py:564+`）
  - 测试：`backend/tests/unit/test_permission_rules.py`、`test_permission_whitelist.py`
- **审批**：`once` / `always` / `reject`（契约 `contracts/v2.py:17`，实现 `api/v2/session.py:821-853`）。
  - `always` 会把资源写入用户级保存规则（`repo.save_permission_rule`），不是项目级 JSON。
- **Policies（硬策略）**：
  - `backend/policy/engine.py`
  - 动作：`provider.use/tool.use/integration.connect/integration.use/mcp.use/skill.use/plugin.use/websearch.use` + 权限动作
  - 来源优先级 global < project < agent < managed；最后匹配生效；无匹配允许
  - 只能收紧：`AccessController` 先权限后策略（`execution/access.py`）
  - API：`GET /api/v2/policies`、`POST /api/v2/policies/explain`（`api/v2/policies.py`）
  - 测试：`test_policy_engine.py`、`test_provider_policy.py`
- **多资源严格性**：工具映射到多个 policy action 时，任一 deny 即阻断（`access.py:policy_block`）。
- **Shell 审批**：命令拆分、base 命令、路径 token、`$(...)`/反引号/重定向强制审批（`permission.py:110-232`）。
- **外部路径**：白名单/会话工作区/内部数据目录免审，其余审批；`.git` 拒绝；`permission.py:289-600`。

### 8.2 差距

- **`external_directory` 默认值相反**：OpenCode 默认 allow（不提示外部访问）；本项目默认对白名单外路径审批（更严格，安全差异）。
- **保存批准的作用域不同**：OpenCode 是项目级 `allow` 规则且永不覆盖 `deny`；本项目是用户级 DB 规则。
- **Policies 来源只有 global/agent/manager 排序**，没有真正的项目/系统/托管配置文件来源（因为配置本身不分层）。
- **没有 macOS 托管 plist / Console workspace 策略**。
- **`tool.use` 资源格式**：OpenCode 是 `<action>:<value>`（如 `shell:git push *`）；本项目 `policy_actions_for_tool` 用的是工具名/动作名（如 `tool.use` + `shell_exec`），语义不完全等价。
- **权限规则默认基线**：本项目有 `.env` 等保护，但未见与 OpenCode 完全相同的四条基线（`* allow` + `.env ask` + `.env.* ask` + `.env.example allow`）。
- **没有 `integration.*` / `plugin.use` 的运行时执行点**（动作已定义，但 integration 体系不存在）。

---

## 9. 上下文管理（对应 §11）

### 9.1 已实现

- **压缩（对齐 opencode v2 主路径）**：
  - `harness/compression.py`：阈值 = `context_window - buffer`，buffer 默认 `"15%"` 且至少 16000
  - tail 默认最近 2 个工具轮次（`compaction_tail_turns=2`）
  - 专用压缩 Agent：`harness/compression_agent.py` + `prompts/system/compression_agent.md`
  - 自动/手动 compact 共用 `ContextCompactor` / `compress_messages`
  - 不物理删除历史：写 `context_summary` + `context_tail_start_id` + `session_messages(type=compaction)`
  - 配置：`compaction_auto/keep_tokens/buffer/tail_turns/min_gain_tokens/compress_model/...`
  - 测试：`test_compaction_alignment.py`、`test_compaction_checkpoint.py`
- **快照与回滚**：
  - 影子 git 对象库：`app/snapshot/snapshot.py`
  - 轮次级 before/after tree + 外部文件归档：`app/snapshot/turn.py`
  - diff/restore：`app/snapshot/vfs.py`、`api/v2/session.py:711-747`
  - 前端「回滚到此处 / 查看改动 / 还原文件改动」：`V2ChatView.vue:508-548`
- **附件**：
  - 图片多模态内联 + 缩略图 + OCR/caption：`app/image_processor.py`、`app/attachment_processor.py`
  - PDF/Word/Excel 落盘 + `.extracted.txt`：`attachment_processor.py`
  - `file://` + `#Lx-Ly` 行范围：`execution/uri.py`、`api/attachments.py`
  - 图片尺寸/字节限制：`image_max_dimension=4096`、`image_max_bytes=20MiB`
- **References**：
  - 本地路径 + Git 仓库缓存/克隆/刷新/隐藏/树：`backend/references/__init__.py`
  - 24h 刷新常量、`git clone --depth 1`
  - API：`/api/v2/references`（list/add/delete/refresh/tree）
  - 测试：`test_references.py`

### 9.2 差距

- **无 Provider 原生压缩（native）**：没有 `compaction: {type: native}`，也没有不透明加密检查点。
- **无后台压缩（background_compaction）**：没有阈值前预写摘要并换入的机制。
- **无「第二次 overflow 直接报错」的原生语义**：本项目 overflow 会先压缩重试，再降级剥媒体/裁剪（`graph/nodes/base.py`），更保守。
- **快照不是 OpenCode 的 `/undo`/`/redo` 双命令**：本项目是「回滚到某条消息 + 恢复该轮文件改动」，没有全局 undo/redo 栈。
- **快照不覆盖**：Git 忽略文件（本项目外部归档兜底部分覆盖）、大文件、活动目录外文件（有外部归档但上限 20MiB）；与 OpenCode 的 ≤2MiB 未跟踪文件口径不同。
- **没有 `snapshots: false` 配置开关**；影子仓库按 git 项目自动启用。
- **References 刷新策略较简单**：启动时 `missing/stale` 判定 + 手动 refresh；没有「新 prompt 准入后后台检查、距上次 ≥24h 才刷新」的完整调度。
- **References 别名校验**：未见空名/`/\`/空白/反引号/逗号拒绝的显式校验。
- **附件限制**：OpenCode 单项 20MiB、Desktop 总 20MiB；本项目有 20MiB 图片上限和文本提取上限，但无 Desktop 总限额概念。

---

## 10. 指令、技能与命令（对应 §12）

### 10.1 指令（Instructions）— ✅ 基本完整

- 发现顺序：global → project 向上 → config → nested；`backend/prompts/instructions.py:7-17`
- 全局候选：`~/.config/agentsuper/AGENTS.md` + 平台配置目录 `AGENTS.md`（`instructions.py:115-120`）
- 项目向上搜索到 home，外层优先（`:133-146`）
- config sources 支持 file/glob/`~/`/绝对/URL，URL 5s 超时（`:234-346`）
- 去重、mtime/size 变更检测、token 预算复用 `cognition/context_meter.py`（`:85-112`、`:411-441`）
- 嵌套 `AGENTS.md` 由文件工具访问登记后按需加载（`execution/tools/file_tools.py:25-32`、`instructions.py:159-188`）
- 注入位置：稳定头之后（`prompts/assembly.py:46-77`）
- API 只回元数据：`backend/app/api/instructions.py:20-46`
- 配置：`instructions_enabled/instructions/instructions_max_chars/instructions_max_tokens`（`config.py:323-329`）
- 测试：`backend/tests/unit/test_instructions.py`

**差距**：
- 路径是 `.agentsuper`，不是 `.opencode`（品牌化差异）。
- 没有「会话运行中编辑全局/向上 AGENTS.md，下一请求前检测变化并插入更新」的明确 turn-level 集成测试（单元有 mtime 检测）。

### 10.2 技能（Skills）— 🟡 部分

- 发现：`~/.claude/skills`、`~/.agents/skills`、`~/.config/agentsuper/skills-external`、`<ws>/.claude/skills`、`<ws>/.agents/skills`、`<ws>/.agentsuper/skills`（`app/skills/discovery.py:8-27`）
- `SKILL.md` frontmatter：`name/description/enabled/disable-model-invocation`（`app/skills/loader.py:21-49`）
- 技能 ID：嵌套目录→目录名，顶层→文件名（`loader.py:181-202`）
- 模型可调用：注册 `load_skill_<name>`，跳过 disabled / disable-model-invocation（`app/skills/tools.py:28-52`）
- 直接调用：`/skill-name`、消息开头 `@skill-name`（`app/skills/slash.py:55-105`）
- 权限：`load_skill* → skill / skill.use`（`execution/actions.py:57-58`）
- 远程 catalog：`app/skills/catalog.py`、`POST /api/v2/skills/catalog`
- 测试：`test_skills_registry.py`、`test_skill_tools.py`、`test_skill_catalog.py`

**差距**：
- **没有支持文件加载**：只读 `SKILL.md` 正文，没有「技能基目录 + 最多 10 个支持文件路径示例」。
- **`/skill` 与 `@skill` 直接注入绕过工具循环**，因此不经过 `skill.use` 策略；只有模型调用 `load_skill_*` 时策略生效。
- `@skill-id` 只在消息开头识别，不支持行内提及。
- 无 `.opencode/skills` 路径（兼容路径是 `.claude`/`.agents`）。
- 优先级与 OpenCode 的六层顺序未完全一致。

### 10.3 命令（Commands）— 🟡 部分

- Markdown 命令：`~/.config/agentsuper/commands/*.md`、`<ws>/.agentsuper/commands/*.md`
  - `backend/app/commands/registry.py:1-14`
- 嵌套名：`/team/review`（`app/skills/slash.py:10-17`）
- 参数：`$ARGUMENTS`、`$1..$9`、`@file`、`files` glob（`registry.py:47-55`）
- `agent` / `model` frontmatter 被消费（`app/skills/slash.py:84-88`、`app/commands/context.py`、`app/deps.py:1135-1141`）
- 命令先于技能/插件解析（`slash.py:74-88`）
- API：`GET /api/v2/commands`（`app/api/commands.py`）
- 前端斜杠面板合并命令 + 技能（`V2ChatView.vue:161-179`、`:894-922`）
- 测试：`backend/tests/unit/test_commands.py`

**差距**：
- **`template:` frontmatter 不支持**；正文即模板。
- **`subagent:` 不支持**；`subtask` 被解析但无运行时消费者。
- **`!`shell`` 不执行**（有意安全差异，`registry.py:12-13`）；OpenCode 是执行后插入输出。
- 无命令热重载/watch。
- 无 `commands{}` 内联 JSONC 配置。
- 无 `subtask/template/!shell/嵌套命令` 的测试。

---

## 11. 插件系统（对应 §13）

### 11.1 已实现

- **Python 插件加载器**：`backend/extensions/plugins.py`
  - 扫描 `plugins/*.py`，读取 `PLUGIN_NAME/VERSION/DESCRIPTION/OPTIONS/PERMISSIONS/HOOKS`
  - 支持同名 `.json` manifest（`plugin.json`）
  - `.enabled` 文件控制启用
- **Hook bus**：`backend/extensions/hooks.py`
  - 事件：`tool.execute.before/after`、`chat.message`、`permission.ask`、`session.start/end`、`tool.register`
  - 按优先级顺序执行，异常隔离
- **工具注册**：插件 `tool_*` 函数由 `sync_plugin_tools` 注册进 `ToolRegistry`。
- **卸载**：`unload` 清理 hook 与工具。
- **API/UI**：`/api/plugins` 旧路由仍在挂载（`app/main.py:66`）；导航有 `/plugins`。
- **文档明确非目标**：JS/TS 插件、GUI/TUI/RPC part 见 `docs/adr/0003-js-plugins-and-native-compaction.md`。

### 11.2 差距

- **没有 OpenCode 的四部分插件模型**：Server / TUI / GUI / RPC。
- **没有 `Plugin.define` / `PluginContext` / transforms**：
  - 无 `ctx.agent/provider/model/command/integration/mcp/reference/skill/tool/vcs/worktree/websearch.transform`
  - 无 registry 重放式可变视图
- **Hook 面窄**：没有 `prompt`、`context`、`compaction`、`generate`、`title`、`model.request`、原生 HTTP hooks。
- **没有插件存储 `ctx.storage`**（set/get/remove/scan）。
- **没有 RPC 定义/调用/`POST /api/rpc/{rpcID}/{method}`**。
- **没有插件自动发现 `.opencode/plugins/` 目录、`plugins[]` 包名/`file://`、`-`/`*`/`.*` 控制、内置不可移除插件**。
- **配置只有 `plugins_dir`**（`config.py:200-201`），没有 `plugins` 列表与 options。

---

## 12. API、Client SDK 与嵌入式 SDK（对应 §14）

### 12.1 已实现

- **Web Chat v2 API 子集**（`backend/app/api/v2/`）：
  - session：create/list/get/patch/delete、prompt、inbox、interrupt、message、compact、revert、diff、restore、fork、model、agent、permission、form、event
  - agents / config / mcp / policies / references / network / websearch / skills catalog / local models
- **事件流**：单一 `GET /api/v2/event`，支持 `types` 过滤、`server.connected`、durable outbox、15s ping、断线重连（`api/v2/session.py:882-940`、`frontend/src/v2/eventStream.ts`）。
- **OpenAI 兼容**：`/v1/chat/completions`、`/v1/models`（`app/api/openai_compat.py`），支持流式与 `X-Session-Id` 续会话。
- **旧 `/api/chat/*` 契约**：保留 `compat.py`（裸 `/chat` + SSE），供旧前端/测试。
- **前端 v2 运行时**：`v2/sessionStore.ts`（hydration + 幂等 reducer）、`v2/eventStream.ts`（单连接 SSE）、`V2ChatView.vue`（唯一时间线）。

### 12.2 差距

- **API 规模远小于 OpenCode 的 ~170 operations / 497 schemas**。本项目 `backend/app/api/v2/` 共 57 个路由，没有：
  - Server `/api/info`、`/api/pair`、`/auth/connect/{code}`
  - Location `/api/location`、`/api/location/reload`
  - Project / Credential / Integration / Form（仅 session form）、Filesystem、Command（有 `/v2/commands`）、Skill 管理、RPC、Event 历史、PTY / Persistent PTY、Shell、Worktree、VCS、Debug、Migration、Usage、Cache
- **没有 Client SDK（`@opencode/client`）**。
- **没有嵌入式 SDK（`@opencode/sdk`）**：不能进程内组装 server、走内存 router。
- **没有 ACP**（`opencode acp`）。
- **事件流没有 `Last-Event-ID` 回放**；OpenCode 文档也说不重放，但本项目有 durable outbox，理论上可补，当前未接。
- **`server.connected` 只有 `version: "2"`**，没有 OpenCode 的完整 server/location 元数据。

---

## 13. CLI 与运维（对应 §15、§16）

### 13.1 已实现

- **日志**：`observability/`、`api/logs.py`、前端日志页；LiteLLM debug 日志（`observability/litellm_debug.py`）。
- **网络/代理/CA**：`backend/net/egress.py`、`api/v2/network.py`（代理、CA、超时、诊断）。
- **健康检查**：`/api/admin/health`（`app/api/admin.py:118`）。
- **Docker/K8s/Caddy/nginx**：`backend/deploy/`。
- **监控**：`observability/monitor.py`、前端 `/monitoring`。

### 13.2 差距

- **没有 CLI**：`opencode run/session/auth/models/mcp/plugin/stats/serve/pair/service/reload/api/acp/debug/upgrade/uninstall` 全部不适用。
- **没有 service 管理**（start/restart/status/stop/get/set/unset）。
- **没有堆快照 `debug heap-dump`、SIGPROF/SIGUSR1 性能分析**。
- **没有 `debug agents/config/paths`** 对应命令；有配置页与 API，但不是 CLI。
- **没有 `opencode stats`**（按天/模型/成本统计）；有 usage/observability，但不是同口径 CLI。

---

## 14. 实现要点对齐（对应 §17）

| OpenCode 设计模式 | 本项目 | 说明 |
|---|---|---|
| 客户端-服务器 + Location 作用域 | 🟡 | 有服务端真相源；无 Location 懒加载/空闲卸载 |
| Transform + Reload 可重建注册表 | ❌ | Python 插件直接改注册表，无重放式 transform |
| 分层权限流水线 | ✅/🟡 | `AccessController` 权限→策略；但缺 external_directory 默认 allow 与 project 保存批准 |
| 上下文预算管理 | ✅ | `ContextBudget` 统一口径 + 压缩 tail/buffer |
| 事件驱动客户端同步 | 🟡 | 单连接、过滤、durable；无 `Last-Event-ID` 回放 |
| 可扩展性边界 | 🟡 | 插件不沙箱、技能/命令不绕过权限；但 `/skill` 直接注入绕过策略 |
| 快照与撤销边界 | 🟡 | 影子 git + 外部归档；无全局 undo/redo，配置开关缺失 |

---

## 15. 测试与验证

本次实际执行的抽查（后端）：

```powershell
cd backend
.venv\Scripts\python.exe -m pytest `
  tests\unit\test_policy_engine.py `
  tests\unit\test_mcp_runtime.py `
  tests\unit\test_references.py `
  tests\unit\test_permission_rules.py `
  tests\unit\test_instructions.py `
  tests\unit\test_commands.py `
  tests\unit\test_agents.py `
  tests\unit\test_model_variants.py `
  tests\unit\test_local_discovery.py `
  tests\unit\test_event_bus.py `
  tests\unit\test_compaction_alignment.py `
  tests\unit\test_snapshot_vfs_external.py `
  tests\unit\test_formatters.py `
  tests\unit\test_plugin_hooks.py `
  tests\unit\test_attachments_api.py -q
```

结果：**86 passed**（1 条 Starlette/httpx 弃用 warning，无失败）。

已有但本次未跑的相关测试（供后续验证）：

- `backend/tests/unit/test_permission_whitelist.py`
- `backend/tests/unit/test_skill_tools.py`
- `backend/tests/unit/test_skill_catalog.py`
- `backend/tests/unit/test_skills_registry.py`
- `backend/tests/unit/test_formatter_runner.py`
- `backend/tests/unit/test_model_runtime.py`
- `backend/tests/unit/test_model_switch.py`
- `backend/tests/unit/test_session_projection.py`
- `backend/tests/unit/test_session_store_persistence.py`
- `backend/tests/unit/test_v2_persistence.py`
- `backend/tests/unit/test_uri_attachments.py`
- `backend/tests/unit/test_provider_policy.py`
- `backend/tests/unit/test_websearch_policy.py`
- `backend/tests/integration/test_mcp_stdio_real.py`
- `backend/tests/integration/test_agent_part_events.py`
- `backend/tests/integration/test_m3_subagent.py`
- `backend/tests/integration/test_session_workspace_policy.py`

**未验证项**：真实模型、真实 Ollama、前端 `npm test` / `npm run build`、live SSE 集成。本报告不据此声称通过。

---

## 16. 缺口清单与优先级

### P0（影响「像 opencode 的核心闭环」）

1. **配置分层与变量替换**：支持项目/全局/系统配置合并、`{env:}`/`{file:}`；否则每个项目都要改同一个 `config.json`。
2. **MCP OAuth + prompts + 细粒度超时**：当前 OAuth 501，远程 MCP 只能手配 headers；prompts 不能变命令。
3. **命令语义补齐**：`template`、`subagent/subtask`、安全的 `!`shell``（走审批）与热重载。
4. **Agent `request` 与 `steps` 运行时接线**：否则 frontmatter 写了不生效。
5. **技能 `/skill`、`@skill` 走 `skill.use` 策略**：当前直接注入可绕过硬策略。
6. **Formatter 覆盖所有写路径**：`append_file`、`apply_patch`。

### P1（能力完整性）

7. **Worktree 管理**：`worktree_create/list/remove` + `worktree.directory`。
8. **会话 move / export / import / shell / active**。
9. **Policies 真正多源**：项目/系统/托管来源与优先级；`tool.use` 资源格式对齐 `<action>:<value>`。
10. **插件 Transform / 存储 / 更多 hooks**（至少 session `context`、`compaction`、`prompt`、`model.request`）。
11. **Provider compatibility 字段逐项解释**：reasoningField、maxTokensField、cacheFormat、streamUsage 等。
12. **工具补面**：`list_mcp_resources`/`read_mcp_resource` 模型工具、`execute` Code Mode（若产品需要）、image generate（可选）。

### P2（增强/非目标）

13. **原生压缩 / 后台压缩**（ADR-0003 明确保留差异）。
14. **全局 undo/redo** 与 `snapshots: false`。
15. **事件 `Last-Event-ID` 回放** 或显式 `replay_gap` 语义。
16. **Client SDK / 嵌入式 SDK / ACP / CLI / Desktop**（明确非目标，除非单独立项）。
17. **JS/TS 插件、TUI/GUI/RPC part**（ADR-0003 明确不做）。

---

## 17. 明确的安全/产品差异（不是 bug）

这些差异是有意为之，报告不建议直接「对齐」为 OpenCode 默认：

1. **外部目录默认审批**：本项目默认白名单外路径需人工审批；OpenCode 默认允许外部访问。本项目更严格。
2. **命令 `!`shell`` 不执行**：避免绕过工具权限；OpenCode 明确警告它绕过权限。
3. **Shell 静态解析保守**：动态求值/重定向一律强制审批，不做完整 shell 语法扫描。
4. **技能/命令路径使用 `.agentsuper`**：品牌化，不是 `.opencode`。
5. **MCP 工具前缀 `mcp_`**：避免与内置工具冲突，不是 OpenCode 的 `<server>_<tool>`。
6. **无原生/后台压缩**：保留可移植性与可观测性，见 `docs/adr/0003-js-plugins-and-native-compaction.md`。
7. **无 Code Mode / 浏览器工具**：见 `docs/adr/0002-browser-execute-tools.md`。

---

## 18. 审计边界

- 本报告是**静态代码审计**，未运行全量后端/前端测试，未跑真实模型与真实 Ollama。
- 行号基于当前工作区，后续改动可能漂移；以文件与函数名为准。
- 「文档声称已落地」的条目（如 `docs/opencode-gap-matrix.md` 的进度表）与代码有出入时，本报告以代码为准：
  - 例如 gap matrix 声称 `test_instructions_turn`/`test_formatter_on_write`/`test_agent_selection` 存在，实际未找到。
  - gap matrix 声称 MCP OAuth 已 501、插件 JS 不支持，与代码一致。
- 前端只做了入口与事件流审计，未逐组件核对视觉/交互细节。
