# AGENTS.md — Knowledge Base System (Harness)

> 面向 coding agent 的工作手册。人类入口看 [`README.md`](README.md)。
> 架构权威：`docs/harness-architecture-design.md`；差距/验收：`docs/harness-design-gap-matrix.md`。
> 文档与实际代码冲突时，以当前代码 + 测试 + 设计文档为准，并同步修正本文件。

## 0. 先读什么

- **后端主链路**：`backend/app/main.py` → `backend/app/deps.py` → `backend/graph/main_graph.py` → `backend/graph/nodes/`
- **Harness 层**：`backend/harness/`（工具协议、TurnHarness、压缩、切换、预算、错误、安全）
- **前端主链路**：`frontend/src/views/V2ChatView.vue` → `frontend/src/stores/multiAgent.ts`（仅会话列表/目录）+ `frontend/src/v2/`
- **测试**：`backend/tests/`、`frontend/tests/`
- 旧的 `backend/app/agent/**`、`backend/app/context/**`、`backend/scripts/**` **已删除**，不要按旧路径找代码。

## 1. 仓库结构

```text
backend/
  app/
    main.py                 # FastAPI 入口；挂载 /api、裸 /chat 契约、启动清理
    deps.py                 # PlatformContext：会话、图、切换、权限、并发
    api/                    # chat / compat / sessions / documents / models / ...
    session/                # session.db、history、service、repository、projector
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
    tool_history.py         # 旧跨轮工具历史工具函数（注入已退役）
    context.py              # 旧模型视角装配（保留给回滚/测试）
    compression.py          # 压缩计划/摘要/精确值/tail
    compression_agent.py    # 专用压缩 Agent（system prompt + 模型调用）
    switch.py               # SwitchService 状态机
    budget.py               # BudgetLedger
    schema.py               # ToolSchemaCache + 20 上限
    retry.py                # 可重试错误退避
    errors.py               # HarnessError / classify_error
    security.py             # 密钥脱敏
  models/                   # TierModel / LiteLLM / stream / catalog
  cognition/                # compressor / context_budget / token_counter / tool_output
  execution/                # VFS / permission / tool_registry / tools
  observability/            # trace / metrics / cost
  prompts/                  # providers/{_shared,default,anthropic,gpt,gemini,kimi,trinity}.md, system/*.md, nodes/*.md
  tests/                    # unit / contract / integration / e2e / perf / reliability / security

frontend/
  src/
    stores/multiAgent.ts    # 会话、消息、队列、持久化；事件交给 harness
    api/multiAgent.ts       # SSE transport
    views/V2ChatView.vue
    mobile/                 # MobileShell + mobile pages; MobileChat reuses V2ChatView
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

- **后台回合 / 断连续流**：v2 使用 `app/session/coordinator.py` + `/api/v2/event`；旧 `/api/chat/multi-agent/stream` 与 `turn_runner` 已删除。
- **TurnHarness**：`harness/kernel.py`。`PlatformContext.run/stream` 已接入；`turn_id` 通过 contextvar 传播。
- **Model Runtime**：`models/runtime.py`（ADR-0001）。`TierModel` 只做 facade；endpoint/凭证、窗口、`num_ctx`/`think`、`keep_alive`、Provider timeout 与预热都收在 `ModelRuntime.complete/stream/warm`，provider 差异由 `_OllamaAdapter` / `_ProviderAdapter`（云/兼容）承担。解析结果按回合 scope 缓存（`app/api/compat.py` 与 `set_current_turn` 成对 begin/end）；无 scope 的后台调用单次解析。`models/catalog.py` 保持声明式，provider/config 读取有写时失效的进程缓存。
- **AgentLoop**：`run_tool_loop`。`graph/nodes/execute.py` 传 `stream=True`；native 与 fallback 都走 `ToolAdapter.stream()`，正文增量经 `push_event(text_delta)` 实时转 SSE；`app/api/compat._graph_events` 同时等待图节点与事件队列（不再靠 12s 心跳才 drain）。fallback 在 `ToolCallReady` 时提前执行工具；`models/stream.collect_stream` 只扣住疑似工具 JSON，普通正文逐块下发。空回复最多补答 2 次，之后收尾；不把 thinking 模型的 reasoning 冒充答案。
- **工具并发**：同一轮工具调用按 `TOOL_CONCURRENCY`（默认 10）有界并发；`ToolDefinition.mutating` 或 `file_tools.WRITE_TOOLS`/`shell_exec` 拿写锁独占，`READ_TOOLS`（read_file/ls/grep/glob）拿共享读锁——读之间并行，但不与写重叠；自定义插件工具默认 `mutating=True`（保守串行），只读插件可显式传 false。审批提示按回合串行。结果仍按模型 tool_calls 顺序回填。
- **Prompt 前缀缓存**：`prompts/assembly.compose_system` 固定 shared + profile + role 稳定头，vision/memory/RAG/skills 追加在后；tools 选满后按 name 排序，避免注册序抖动前缀；`harness/schema.py` 预留 core 文件/Shell 与 essential global（web/weather/task/ask_question）名额，插件/自定义工具再多也不会挤掉 `get_weather`/`web_*`；`models/cache_hints.py` 对 Anthropic 在 system 与最新 user 注入 `cache_control`，对 OpenAI/Azure/OpenRouter/OpenCode/Venice 注入 `prompt_cache_key` + `x-session-affinity`；提示词文件按 mtime 缓存读取。
- **文件任务证据**：用户要求列目录/读文件，但本轮 `tool_trace` 为空时，禁止返回模型编造的文件清单；统一提示“先执行工具/换 tool-capable 模型”。
- **聊天附件**：图片走多模态内联；PDF/Word/Excel 等落盘到会话工作区 `attachments/`，并生成可读的 `.extracted.txt`（超过 12k 字符按 6k 分片），模型用 `read_file`/`grep` 读全文；模型上下文只内联 `attachment_inline_max_chars_per_file` / `_total` 上限内的正文；聊天记录不再内联正文，前端「查看正文」走 `POST /api/attachments/extract`（上限 `attachment_extract_max_chars`）。图片附件对非视觉模型先 OCR（`image_use_ocr`），提取不到文字再回落视觉 caption（`image_vlm_caption`）；视觉模型直接内联 `image_url`，不走 OCR。DeepSeek 含图片的请求走 `openai/<model>` + DeepSeek `api_base`（litellm 的 `deepseek/` adapter 会丢 `image_url`）；纯文本/工具轮仍走 `deepseek/`。
- **模型切换**：`harness/switch.py`。门禁 → 原文备份 → 摘要/精确值探针 → 原子写 epoch → 持久化 model；失败回滚。
- **本地模型下载（GGUF → Ollama）**：`app/local_models/`（`sources` ModelScope 优先 + HuggingFace 兜底、`metadata` GGUF 头解析自动填参、`downloader` 断点续传/分片合并/取消/配额、`importer` 上传 blob 后 `POST {ollama}/api/create` 的 `files` 映射（分片按原文件名一起上传，旧版 Ollama 回退 `modelfile`）、`store` `data/local_models/jobs.json`）；REST `/api/local-models/{sources,search,files,downloads}`（admin）。**导入后自动做一次 1-token 加载探测**（`importer.verify_ollama_model`）：`/api/create` 只写 manifest、Ollama 首次推理才真正加载，探测能在导入阶段就发现「架构/量化不被本机 llama.cpp 支持」的 GGUF（如新 `qwen35` MTP 头），把任务标为 `error`；前端失败任务可「重试导入」（`POST /api/local-models/downloads/{id}/retry`，复用已下载文件、跳过下载）。`num_ctx` 仅在用户显式设置 `context_length` 时才写进模型参数，不再把模型原生窗口（可能极大）强加到每次请求。导入后登记为目录 `extra`（`origin=extra`），模型列表与聊天选择器自动可见。配置：`local_models_dir`、`local_model_source_priority`、`local_model_download_concurrency`、`local_model_disk_quota_gb`、`modelscope_endpoint`、`huggingface_endpoint`。
- **压缩（对齐 opencode v2）**：`harness/compression.py` 是统一门面；`harness/compression_agent.py` 是纯摘要、无工具的专用压缩 Agent（system prompt `prompts/system/compression_agent.md`，独立模型调用）。阈值 = `context_window - buffer`，`buffer` 来自 `compaction_buffer`（默认 `"15%"`，按 `max(15%×窗口, 16000)` 解析）；tail 默认保护最近 `compaction_tail_turns`（默认 2）个工具轮次，计数尾部由 `keep_recent` 驱动，`compaction_keep_tokens` 默认 0 表示使用 `ContextBudget` 的 preserve budget；摘要请求复用完整对话，8 段模板见 `prompts/nodes/compress.md`；移除 LLM 门禁，模型可用即生成摘要，失败/手动压缩失败降级确定性摘要；自动、手动 compact、模型切换共用 `ContextCompactor`/`compress_messages`；auto 图节点使用 `CompactionPolicy.for_auto`，不再有 `compress_graph_messages`；**不再物理删除历史**，只写 `context_summary` + `context_tail_start_id` 与 `session_messages(type=compaction)`；前端显示可折叠压缩卡。相关配置：`compaction_auto`、`compaction_keep_tokens`、`compaction_buffer`、`compaction_tail_turns`、`compaction_min_gain_tokens`、`compress_model`、`compress_model_enabled`、`compress_max_tokens`。
- **会话真相源（v2）**：`session.db` 的 `session_messages`（含 `user/assistant/tool` 与 `message_parts`）是对话真相源；每轮结束由 `app/session/projector.py` 把 checkpoint 增量幂等投影进来，checkpoint 只做单轮运行时。投影按稳定消息 id 去重；assistant 的 `tool_calls` 落为 tool part，tool 结果回填 output；逻辑删除/撤销用 `hidden_ids` 墓碑，防止 checkpoint 重新投影复活；撤销到压缩点之前会同时隐藏该点之后的 compaction 行并清空 epoch 摘要/tail。
- **模型视角重建**：回合前 `PlatformContext.rebuild_context_view` 从 DB 重建 `ContextView`（`[最新 compaction 摘要] + [tail_start_id 之后原文]`；`db_view` 与 LangChain message 视图共用一次构建），以 `Overwrite([...view.messages, 当前 user])` 写入 state；tail 内保留原生 `assistant(tool_calls)` + `ToolMessage(tool_call_id)`，更早历史只进摘要。DB 无该会话时回退 checkpoint。
- **Token 估算**：`cognition/context_meter.py` 是唯一口径；按模型选择计数器（DeepSeek 原生 tokenizer；其它 provider/Ollama 用 tiktoken 代理）并维护校准 ratio，用消息 fingerprint + 工具 schema 哈希缓存。`execute` 节点写入 `state.context_system_tokens` / `context_tool_tokens`，`graph/routers._context_tokens` 用它们加上 messages（并读 `state.model`）判断压缩阈值；`models/context_guard.py`、`models/runtime.py`、`graph/nodes/base._maybe_prune_tool_outputs`、`harness/compression.estimate_messages` 也消费同一口径。不要新增第二套 tokenizer/估算分支。
- **上下文视图**：`graph/nodes/execute._conversation_history` 直接消费已重建的 `state.messages`；跨轮 `tool_history` 回插已退役（模块保留但不再注入）。
- **窗口预算口径**：`cognition/context_budget.py` 的 `ContextBudget.plan` 统一 output reserve、usable、preserve、min_gain、prune gate 与 Ollama transport reserve/slack；`harness/compression.py`、`models/context_guard.py`、`graph/nodes/base.py` 都消费它，不要再各写一套比例。
- **溢出恢复**：`run_tool_loop` 捕获 `ContextOverflowError` 后先用 `harness.compression` 压缩对话并重试一次；摘要失败再降级剥媒体/裁剪。Ollama 仍由 `models/context_guard.py` 先行自动扩窗。
- **Ollama prompt 适配**：`models/context_guard.py`，在 `TierModel` 传输层（`raw_invoke/raw_ainvoke/raw_stream`）执行。窗口 = 显式 override > `/api/ps` last-known > `OLLAMA_NUM_CTX`；输出预留 `max(512, 25%×窗口)`，估算裕量 `max(256, 8%×窗口)`；超预算先 `truncate_messages` + `sanitize_tool_messages`，保留 system + 最新 user；system+工具+当前 user 仍装不下时**自动扩展**：按所需最小窗口（2 的幂，上限 32768）随请求下发 `num_ctx`，让 Ollama 重载到更大窗口；只有超过自动上限才抛 `ContextOverflowError`。真实 `prompt_tokens` 按模型 EWMA 校准估算比；一次 overflow 会清窗口缓存、重探、重试一次。`OLLAMA_CONTEXT_GUARD=0` 关闭。
- **治理**：`BudgetLedger`、`classify_error`、`retry_async`、`redact_secrets`、`ToolSchemaCache`。
- **Web Chat v2（完成）**：`backend/app/api/v2/` 提供 session/prompt/inbox/message/interrupt/compact/revert/fork/model/agent；`app/session/coordinator.py` 串行 drain durable inbox；`app/events/` 提供全局事件总线与 durable outbox，`GET /api/v2/event` 为 SSE。旧 turn SSE 已删除；轮次快照持久化在 `session_snapshots`。
- **LiteLLM 调试**：`observability/litellm_debug.py`，默认 `litellm_debug=true`；启动时 `litellm._turn_on_debug()`，日志写 `data/logs/litellm-debug.log`（20MB×3 轮转，`redact_secrets` 过滤）。控制台仍保持 INFO；`LITELLM_DEBUG=0` 关闭。
- **寒暄/自我介绍**：`filter`/`plan` 对 greeting/thanks/ack/test/intro 做全串锚定；intro 复用 `SELF_INTRO_MAIN`，命中即 `final_output`，零模型调用。不要用“包含介绍”这种宽匹配，避免误伤“介绍这个文件”。

### 2.2 前端（Harness 首版）

```text
V2ChatView
  -> v2/sessionStore (session runtime + idempotent reducer)
  -> v2/eventStream (single shared SSE)
  -> /api/v2/session/* + /api/v2/event
```

- **桌面外壳（opencode web 风格）**：`App.vue` = `AppTitleBar`（36px 顶栏）+ `Sidebar` + 主内容；移动端仍走 `MobileShell`，不渲染顶栏。
- **标签页**：`stores/tabs.ts` + `AppTitleBar.vue`。所有功能页与会话页都作为可关闭标签打开；关闭当前页自动跳到相邻页（无标签回主页 `/multi-agent`）；标签持久化在 `agentsuper:app-tabs-v1`。主页 `/multi-agent` 不建标签，由顶栏主页按钮进入。
- **用户设置**：`UserMenu.vue` 顶栏右上角触发；用户卡/昵称/头像/用量/背景色/`SettingsPanel` 全在其中，Sidebar 不再承载用户资料。
- **侧边栏**：工作目录是第一入口。`MultiAgentChatHistory.vue` 直接按白名单目录分组并内嵌会话（可折叠、目录内新建会话、移出目录、临时会话组）；功能页放在底部紧凑导航（`config/navigation.ts`，已移除「多智能体」导航项）。添加目录弹窗 `DirPickerModal` 仍在工作目录标题右侧。
- `frontend/src/harness/events.ts`：`HarnessEvent` / `TurnState`。
- `frontend/src/harness/adapter.ts`：legacy 事件补 `turn_id`，缺 `seq` 保留 undefined。
- `frontend/src/harness/reducer.ts`：`turn_id + seq` 去重、`step_id` 幂等 upsert、agent/text/permission/error/terminal。
- `frontend/src/api/v2.ts` + `frontend/src/v2/`：Web Chat v2 前端基础（REST client、单连接 SSE、sessionRuntime、幂等事件 reducer）；`/multi-agent`、`/multi-agent/:id`、`/v2/chat` 已使用 `V2ChatView`，桌面与移动端共用。
- **本地模型下载（模型管理）**：`ModelManagerView.vue` 的「本地模型」区块 + `stores/localModels.ts`（1.5s 轮询任务，导入完成后刷新模型目录）+ `api/localModels.ts`；聊天选择器通过 `origin=extra` 自动可见。
- **前端设计文档章节尚未补**；当前以代码与测试为准。

### 2.3 opencode 能力层（G-01…G-06）

- **指令**：`prompts/instructions.py` 统一发现 global/向上/project/嵌套/config/URL，`PlatformContext.instruction_blocks/instructions_text` 每轮解析；`compose_system(instructions=...)` 只追加在稳定头之后。指令工作区对齐 opencode：会话未绑定或落在自动 temp 工作区时，`detect_project_root`（`.git` 优先、其次 `AGENTS.md`）决定项目根，不再从 temp 解析。API `GET /api/instructions` 只回元数据；前端来源面板可按项目根维护 `.agentsuper/instructions.json`（`POST/DELETE /api/instructions/entries`，`PATCH /api/instructions/settings` 控制 `auto_discover`；仅允许工作区内文件/相对 glob/http(s) URL），文件选择走 `/api/permission/browse?files=true`。关闭 `auto_discover` 后不再注入 AGENTS.md，只加载显式条目。
- **命令**：`app/commands/registry.py` 发现 `~/.config/agentsuper/commands` 与 `<ws>/.agentsuper/commands`，支持嵌套名、`$ARGUMENTS`/`$1..$9`/`@file`/`files`、`template` frontmatter、`subtask`/`subagent`（隔离派发）、安全 `` !`cmd` ``（仅注入 runner 才执行）；`slash.py` 先解析命令再技能/插件；`subtask: true` 的命令经 `graph/nodes/subtask.py` 在独立上下文运行（`filter → subtask → synthesize → memory_store`），不带父会话历史；API `GET /api/v2/commands`；前端斜杠面板合并命令。
- **格式化**：`execution/formatters/`（spec/builtin/checks/service），仅 `write_file`/`edit_file` 后运行，`shell=False`、超时、首成功即停；事件 `formatter_applied`/`formatter_failed`。
- **MCP**：`mcp/{config,client,runtime}.py` 真 stdio/HTTP JSON-RPC；工具名 `mcp_<server>_<tool>`；API `/api/v2/mcp` 含 CRUD、tools/resources/read/prompts/get/instructions、reload、connect/disconnect；`McpSettingsView` 可浏览并带参数调用 prompts。HTTP 服务器支持 OAuth 2.1：`mcp/oauth.py` 做 RFC9728/RFC8414 发现、PKCE S256、RFC8707 `resource`、RFC7591 动态注册或预注册 client、refresh；令牌存 `state/mcp-oauth.json`（尽力 0600，禁止日志打印），回调 `GET /api/v2/mcp/oauth/callback` 免登录、靠 state 防伪；`POST/GET/DELETE /api/v2/mcp/servers/{name}/auth` 启动/查询/清除授权。OAuth 发现/令牌端点走 SSRF 校验，本地授权服务器需 `SSRF_ALLOW_INTERNAL=true`。
- **Agents**：`agents/registry.py` 支持全局/项目 Markdown、嵌套 ID、mode/model/system/tools/permission/steps/hidden/color/disabled；`request` 只经 `request_kwargs()` 的显式白名单（temperature/top_p/top_k/max_tokens/stop/seed/response_format 等）透传，未知键与 harness 保留键丢弃；`resolver.py` 回退 `build`；内置 build/plan/general/explore/compaction，title/summary 显式 disabled；会话 agent 影响 execute 的 prompt/tools/model/steps 与工具权限。
- **策略**：`policy/engine.py` 来源优先级 global<project<agent<managed、最后命中生效；`execution/actions.py` 统一动作，`policy_block` 在工具执行前硬拦；API `GET /api/v2/policies`。
- **前端入口**：`/agent-settings`（Agents/命令/策略/指令/引用/配置）与 `/mcp`；导航见 `config/navigation.ts`。
- **扩展治理**：`references/`（本地/Git 引用，异步 clone）；`net/egress.py`（统一代理/CA/超时）；`harness/warming.py`（空闲保活）；`execution/tools/websearch_policy.py`（provider/随机/429 冷却）；`models/variants.py`（`#variant` 合并）；`models/local_discovery.py`（LM Studio/vLLM）；`extensions/hooks.py`（插件 hook bus）；`execution/tools/patch_tools.py`（unified diff）；`execution/tools/question_tools.py`（`ask_question` durable form）；`execution/uri.py`（`file://#Lx-Ly`）。
- **明确不做/保留差异**：JS/TS 插件、browser/execute/worktree 工具、`always` 审批、background/native compaction；见 `docs/adr/0002-browser-execute-tools.md`、`docs/adr/0003-js-plugins-and-native-compaction.md`。
- **新增 API**：`/api/v2/references`、`/api/v2/config`（PATCH/rollback）、`/api/v2/network`、`/api/v2/websearch`、`/v1/chat/completions`。

## 3. 开发命令

### 3.1 后端

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 启动约 30–40s；用 /api/auth/status 轮询就绪。
# 不要依赖 --reload（E: 盘热重载不可靠）；改代码后杀进程树再起。
```

- **启动/重启服务必须报进度**：任何启动或重启后端（`uvicorn` / `taskkill` 后重起）都必须分步输出进度，并**每约 5 秒**轮询一次 `/api/auth/status` 直到就绪；禁止一次长时间等待且无反馈。后台跑测试脚本同理：先启动、再周期性读取日志汇报进度。
- **配置来源**：`backend/config.example.json` → 复制为 `backend/config.json`（JSONC，已被 gitignore）。
  优先级：操作系统环境变量 > `config.json` > `config.py` 字段默认值；`.env` 文件不再读取。
- **分层配置与会话作用域**：`config_layers.py` 合并 system → global → project → app（`backend/config.json` 最高）；project 从会话 workspace 向上找 `.agentsuper/config.json`。回合入口用 `bind_workspace_settings(session.directory)` 绑定 contextvar，运行时通过 `get_settings()` 读取；无 project 配置时复用进程级 `settings`。`storage/*`、认证、DB/checkpoint、日志/观测、启动期图构建仍读进程级配置。project 层禁止覆盖 `agentsuper_*`/`auth_*`/`db_*`/`checkpoint_*` 与存储/权限/SSRF 等进程级键。
- **统一数据根**：所有运行数据走 `storage/paths.py`；`agentsuper_data` 空 = 平台用户目录（Windows `%LOCALAPPDATA%/agentsuper`、macOS `~/Library/Application Support/agentsuper`、Linux `~/.local/share/agentsuper`）。`cache/config/state/logs/bin` 默认跟随数据根；相对路径基于数据根，旧的 `data/` 前缀自动去除。首次启动若新根为空，会自动全量拷贝旧 `backend/data`（源保留，`agentsuper_migrate_legacy=false` 关闭）。
- 新增配置项必须同时更新 `config.example.json` 与 `tests/unit/test_config_example.py`（完整性测试会失败）。

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
- `replay_gap`：续流时事件日志已截断，部分历史缺失；前端渲染内联提示，不参与正文。
- `context_truncated`：Ollama prompt 被裁剪时推送（`window`/`budget`/`reserve`/`estimated_before`/`estimated_after`/`dropped_messages`）；`compat` 透传，前端渲染为时间线内联提示，不进入正文。

### 4.2 工具调用

- 统一结构：`ToolCall(id, name, arguments: str)`；Loop 不感知厂商格式。
- 确定性 tool id：`call_{seq}_{sha1(name+args)[:8]}`。
- 原生路径：`tool_calls`；回退路径：定界符解析；两者都经 `normalize_history`。
- 参数非法 → 错误作为工具结果回灌，不崩溃。
- 未知工具/权限拒绝 → 明确错误文本，不执行。
- 工具输出先过 `bound_tool_output`（头尾保留 + 落盘 `data/truncation/`），再进 `tool_history`。存量回收统一走 `ToolOutputPruner.prune(messages, PrunePolicy)`；settings、meter 门禁、落盘、`data.prune` DB 元数据与幂等都在 implementation，caller 只看 `PruneResult`。

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
- **会话存储**：`session.db` 是对话正文真相源；graph checkpoint 只做单轮运行时并双写投影。模型视角每轮从 DB 重建，不要绕过 `app/session/projector.py` 直接拼历史。
- **用户身份**：账号/设备密钥哈希存 `session.db` 的 `auth_users` 表（`storage/schema.py`），不再写 JSON；旧 `auth_users.json` 仅首次访问时一次性导入并删除。
- **`session_inputs`**：执行中追加任务走 `/api/sessions/{id}/prompts` 队列；旧单数 `/prompt` 已删除。
- **权限**：无审批通道时 `require_approval` 工具一律拒绝；`.git` 路径永远拒绝；Shell 重定向目标要过写权限。
- **工具名**：默认 registry 注册的是 `read_file` / `write_file` / `edit_file` / `ls` / `grep` / `glob` / `append_file` / `delete_file` / `create_directory` / `shell_exec` / `task`，以及内置联网/天气 `web_search` / `web_fetch` / `http_request` / `get_weather`；`http_request` 需人工审批。插件工具名以加载器注册结果为准，不要硬编码旧前缀。`backend/plugins` 下的内置插件仍存在，按同名 `.enabled` 文件启用（当前 11 个启用）；与内置工具同名时保留内置工具。`/api/plugins` 路由已挂载，前端「插件管理」页可启停。
- **压缩**：`cognition/compressor.py` 是 canonical；`harness/compression.py` 是统一门面；`harness/compression_agent.py` 是纯摘要 Agent。不要再新增第三套压缩逻辑。
- **自动压缩/手动 compact**：`ContextHarness` + `tail_snapshot` 语义；手动 compact 也要写 tail 起点。
- **模型目录**：`models/catalog.py` 读取能力/窗口；`harness/capabilities.build_profile` 有 60s TTL，人工 override 优先。
- **模型列表口径**：目录条目的只读 `origin`（`override` / `extra` / `provider` / `builtin` / `local`）是「模型管理 → 模型列表」的过滤依据；前端只展示前三类，内置与本机 Ollama 自动探测仅保留在下拉/聊天选择器。Provider 表格「模型数」同口径（`provider_models_source`）。`_provider_seen` 仅在内存记录本次探测实际返回的 ID（含已收录内置 ID），Provider 重建时离线/禁用即清空，不持久化；前端缓存 v2，v1 直接丢弃。要在列表里管理内置/本机模型，需手动添加，或配置一个非保留名的 ollama 类型 Provider。
- **Provider 表单**：Label（显示名）与模型前缀（litellm provider 段）**都必填、互不推导**；前缀新增时校验 `^[a-z0-9][a-z0-9_-]*$`、编辑时只读；后端 `upsert_provider` 同样校验（存量条目不做回溯）。扩展字段 `timeout`/`headers`/`body`/`package`/`compatibility` 由 `catalog._merge_provider_extras` 统一并入 `provider_api()`（openai 与 ollama 分支同口径，回归用例在 `tests/unit/test_ollama_compat.py`）：`headers`/`body` 经 `litellm_extra_kwargs` 映射为 `extra_headers`/`extra_body`，`timeout` 进 `_endpoint()` 由 `_timeout_for()` 消费——**保存即参与运行**，不要再新增旁路。空 timeout（None/""/0）= 清除字段回退默认，不会钳成 1 秒。`provider_models_source()` 会回传这些字段（编辑表单回填依赖）；前端 `ModelManagerView` 的 timeout 输入是 `type="number"`，v-model 会把值变成 number，提交侧必须按 String 处理再 trim。
- **保留前缀三段式（ollama/ollama_chat）**：`upsert_provider`（后端）与 `frontend/src/utils/providerForm.ts`（前端）同规则：`ollama_chat` 前缀任何 type 拒绝；`ollama` 前缀 + type=openai 拒绝（litellm 前缀路由无解）；`ollama` 前缀 + type=ollama **允许**（Ollama Cloud 等远端 Ollama 协议端点）。该组合下 `catalog.provider_api()` 注册表命中**优先于 litellm 前缀**：改写 `litellm_model=ollama_chat/<name>` + api_base/key 走 `/api/chat`，并打 `ollama_registered: True` 标志；`runtime._is_local_ollama_endpoint()` 据此按 **api_base**（localhost/127.0.0.1=本地，否则云端）选 adapter，云端即 `_CloudAdapter`（无 warm/num_ctx/keep_alive、timeout 120 而非 300）。防劫持守卫：provider 名为保留前缀时，`origin` 为 builtin/local 的本地条目**不**被注册表命中劫持（继续走本地 Ollama 分支），未收录 id 与 Provider 探测条目按注册表解析。
- **Ollama 窗口**：默认不下发 `num_ctx`，以 Ollama 服务器配置为准；运行时窗口由 `models/runtime.py` 在后台刷新 `/api/ps.context_length`（30s 快照 + per-model last-known），模型调用路径只读快照、不阻塞事件循环；未加载时回落 `OLLAMA_NUM_CTX`（默认 4096）。只有「模型管理」显式设置过 `context_length` 才下发覆盖；`/api/models` 的 `runtime_context_length` 是只读展示字段。prompt 适配守卫（`models/context_guard.py`）会按该窗口裁剪；**若 system+工具+当前消息仍装不下，会自动把 `num_ctx` 提升到所需窗口（2 的幂，上限 32768）并粘住该值**，只有超过上限才报错。不要在回落值上再假设 8192。本模型空闲驻留由 `OLLAMA_KEEP_ALIVE`（默认 `"30m"`）控制；模型切换成功后会后台预热。数字串（如 `-1` / `0`）在 runtime 内归一化为整数再下发，避免 Ollama 的 Go duration 解析报 `missing unit in duration "-1"`。
- **上下文窗口**：未知窗口时 `resolve_mode` 保守回退；不要在未知窗口上做原生工具假设。
- **Ollama**：`ollama/*` / `ollama_chat/*` 已适配；tools 模型走原生路径（流式工具调用由 stream 层转非流式），无 tools 模型走文本回退。本地 CPU 慢，默认不设为默认模型；冒烟用 `HARNESS_OLLAMA_TEST=1` + `HARNESS_OLLAMA_TOOLS_MODEL` / `HARNESS_OLLAMA_TEXT_MODEL`。
- **不要重新引入**：`MAX_STEPS`、`SUB_AGENT_TIMEOUT`、`SUPERVISOR_TIMEOUT`、`AGENT_TIMEOUT_MULTIPLIERS`、`DOOM_LOOP_*`；这些字段/逻辑已删除。
- **不要引用**：`backend/scripts/`（整目录已删除）、旧 `backend/app/agent/**`、`app/context/**`、`scripts/compact_session.py`。

## 6. 前端陷阱

- **store 公共 API**：`loadConversation` 等会话元数据 API 保留；旧 `send/resume/attachTurn` 已移除，UI 使用 v2。
- **断线续接**：由 `v2/eventStream` + `sessionRuntime` hydration 处理；不要重新启用旧 `resumeSession/attachTurn`。
- **不要重置消息列表**：`model_switched` 只更新后续 turn 的模型；消息 key 用稳定 id。
- **持久化**：`text_delta` 不写 IndexedDB；queued/done/turn_done/error 等阶段才写。服务端是唯一真相源，缓存只做离线回退。
- **权限**：`permission_request` 归一化后交给 `PermissionStore`；不要塞回 reducer 里弹窗。
- **排队**：`turn_done` 由 store 决定 drain/下一轮；controller 只产出终态。
- **移动端**：`MobileChat` 复用 `V2ChatView`；改聊天逻辑时先确认移动端不被破坏。
- **模型列表过滤**：`ModelManagerView` 的表格/搜索/分页用 `utils/modelOrigin.ts` 过滤；默认/轻量/图片下拉保持全量。聊天输入框选择器（桌面 `modelManager.modelOptions` / 移动 `multiAgent.modelOptions`）用 `chatSelectableModels`，只展示已配置 Provider 实际返回/手动配置的模型（`origin` 为 override/extra/provider）外加显式默认模型，避免内置目录里与 Provider 无关的条目混入；没有 Provider 模型时回退全量。
- **Chat surface**: `V2ChatView` is the single timeline; do not reintroduce per-agent blocks as the main render path.
- **只展示真实工具**：时间线过滤 `!step.tool_name` 的 Harness 图内节点（filter/plan/rag/execute_subagent 等）；不要渲染 `NODE_LABELS` 或 Agent 过滤 chips。
- **工具步骤**：默认紧凑行，点击展开；不要默认展开所有参数/输出，避免长任务淹没最终答案。
- **审批**：聊天页用 `PermissionInlineCard`，`Sidebar` 中的模态在 `/multi-agent` 路由隐藏；不要两套同时弹。
- **Context truncation notice**: `context_truncated` -> reducer notice part -> `V2ChatView` inline row; do not merge into the answer.
- Legacy chat components (MultiAgentView / ConversationTimeline / MultiAgentResponse / AgentPanel / ChatInput) were removed after v2 became the default route.
- **构建**：没有独立 lint script；`npm run build` 内含 `vue-tsc -b`。

## 7. 新增/修改时的落点

- **新 API**：`backend/app/api/` 加 router → `app/main.py` include；前端 `frontend/src/api/` 加 client → `errors.ts` 统一解包。
- **新工具**：`execution/tool_registry.py` 注册；内置工具用 `register_function`；更新工具描述；权限/沙箱/输出边界要一起考虑。
- **新 Skill**：内置技能放 `backend/app/skills/bundled/`（Markdown + frontmatter，随包发布、播种进受管库 `data/skills`）；用户自建走「技能」页写受管库；外部源需手动追加，不再自动扫描 `backend/skills/`。
- **自定义工具**：统一走前端「自定义工具」页（`/api/custom-tools`）。脚本型写入 `backend/plugins/custom_*.py`，由 `PluginLoader` 加载后经 `sync_plugin_tools` 注册进 `ToolRegistry`；`.enabled` 文件控制启用。旧的「插件」页/`/api/plugins` 已删除，不要再新增第二套管理入口。
- **新前端事件**：先加 `harness/events.ts` 类型 + `adapter`/`reducer` 分支 + 单测，再接 store/view。
- **新提示词**：角色提示词放 `prompts/system/*.md`，provider 行为层放 `prompts/providers/<profile>.md`，节点提示词放 `prompts/nodes/*.md`；运行时由 `PromptLoader.build_agent_prompt(agent_type, model_id)` 按 provider 组装 `shared + profile + role`，provider→profile 映射在 `prompts/loader.py`；`PROMPT_PROFILE_LAYER=0` 可关闭该层。不要把大段规则塞进 Python。

## 8. 验收与已知缺口

当前实测（2026-11-10，本地 Windows，Ollama Cloud / 保留前缀三段式轮）：

- 后端：`pytest tests\unit tests\contract tests\reliability tests\security` **866 passed**（env-gated 用例默认 skip；含 v2 投影/重建测试、Provider extras 与 ollama 保留前缀回归）
- Harness 覆盖率：门槛 90%；上次实测 `92.46%`（2026-11-03，本轮未重跑 cov）
- 前端单测：`89 passed | 118 skipped`（`npm test`；live 用例需后端）
- 前端 live SSE 集成：本轮未重跑（需后端；上次 `119 passed`）
- 前端 `npm run build`：通过
- 真实模型：DeepSeek `deepseek/deepseek-v4-flash → deepseek-v4-pro`；E2E 8 条、PF-01/02/05/06/07 通过
- 浏览器端到端：Playwright（Chromium）可用于前端 UI 复现/验证；前端 token 注入方式为 localStorage 键 `agent_super_user_id` / `agent_super_auth_token`（见 `frontend/src/api/auth.ts`），可绕过登录页

已知缺口：

- **D-01**：预算事件已补，但计费仍是调用后结算，不是严格预扣。
- **MCP OAuth**：已支持 PKCE + DCR/预注册客户端；尚未实现 scope 升级（403 `insufficient_scope`）与 Client ID Metadata Documents（CIMD）。
- **前端 Harness 设计文档章节**尚未补；代码/测试已落地。
- 真实模型/性能测试默认 env-gated；缺 `HARNESS_SMOKE_MODEL_A/B` 时记为 blocked，不要伪报通过。
- `backend/README.md`、`frontend/README.md` 未随本文件一起重写（本轮只更新根文档）。

## 9. 文档索引

- `docs/harness-architecture-design.md`：Harness v2 设计（权威）
- `docs/web-chat-v2-refactor-design.md`：Web Chat v2（durable inbox / message-part / global event）全量重构设计
- `backend/app/contracts/v2.py` + `frontend/src/contracts/v2.ts`：Web Chat v2 契约；共享 fixtures 在 `contracts/v2/fixtures/`
- `docs/harness-design-gap-matrix.md`：G-01~G-16、§13 验收、阶段记录
- `docs/agent.md`、`agent-1.md`~`agent-4.md`：历史设计依据
- `docs/codeact-sandbox-design.md`：CodeAct
- `docs/移动端聊天交互设计要点-ChatGPT范式.md`：移动端聊天
- `README.md`：人类入口

## 10. 会话工作区与权限（2026-10-27）

- **默认工作区**：未指定目录时，会话根为跨平台系统临时目录 `tempfile.gettempdir()/agentsuper/<session_id>`；Windows 为 `%TEMP%`，macOS 为 `$TMPDIR`，Linux 为 `/tmp`。前端不展示这条默认路径。
- **创建时可绑定白名单目录**：`POST /api/sessions` 与 `/api/chat/multi-agent(/stream)` 接受 `directory`；只允许用户白名单目录（含子目录）且必须已存在，后端用 `PermissionManager.resolve_bindable_directory` 校验。`PATCH /api/sessions/{id}` 仍不接受 `directory`：**创建后会话根不可修改**。
- **免审范围（只有三类）**：
  1. 会话工作目录（默认为隔离临时目录；绑定白名单后即该目录，隐式）
  2. 程序内部数据目录：`data/{truncation,codeact,generated,voice,uploads,snapshots}`（隐式，不显示、不可删）
  3. 用户「工作目录白名单」：`前端 侧边栏 → 工作目录` 手动增删，全局生效，持久化于 `data/runtime_workspaces.json`；选中目录后新会话绑定该目录，会话历史按目录分组
- **其余路径一律人工审批**：含 `backend/` 源码与 git 仓库根；`.git` 永远 deny；`.env*` / `*.db` / `permissions.json` 读需审批、写/执行直接拒绝。
- **源码保护优先于白名单**：白名单里即使包含仓库根，写 `app/`、`plugins/`、`skills/`、`config/` 等受保护源码仍需逐次审批。
- **`external_path_default` 已删除**：路径与命令决策不再读取它；免审只认上述三类。
- **审批时长**：仅本回合；目录审批覆盖整棵子树；回合结束失效；不存在「允许并记住」，长期免审只能靠白名单。
- **白名单 API**：`GET/POST/DELETE /api/permission/workspaces` 只操作白名单（不含程序主工作区）；添加要求绝对路径且目录已存在（`create=true` 可显式创建）；重复添加幂等；Windows 大小写不敏感去重。
- **Shell**：默认 cwd 跟随会话 VFS 根；命令一律人工审批（本回合授权后同 base 命令可复用至回合结束）。
- **目录变更**：创建后不可变更；删除会话只清理自动 temp，不删用户目录。
- **测试**：`backend/tests/integration/test_session_workspace_policy.py` 覆盖 temp 默认、创建时绑定白名单/拒绝越界目录、创建后目录不可改、白名单/内部目录免审、源码不自动放行；`backend/tests/unit/test_permission_whitelist.py` 覆盖白名单增删/校验/大小写去重。

