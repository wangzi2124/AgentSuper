# 配置参数与全链路死代码审计（2026-10-29）

- **审计基线**：当前工作树（含配置迁移未提交改动）。
- **范围**：`backend/config.example.json` 全部 115 项；后端主链路；前端 API 调用链。
- **排除**：tests/skills/docs 历史文档（测试专用参数放附录）。
- **口径**：
  - **P0 死代码**：全库无引用，或仅测试引用且非预留。
  - **P1 未接线**：有实现/有参数，但生产链路没通；M4/M5 预留模块也归此级。
  - **P2 文档/行为不一致**：注释承诺与代码不符、占位说明过期等。
- **结论**：审计时 P0 5 项、P1 6 项、P2 4 项；115 个配置项中 7 项“读了但未真正生效”，11 项为测试专用。**调整后**：P0/P1/P2 已处理（Langfuse 已安装并验证回调注册；工具配额已接线；`tenant_quota`/`ab` 保留并标注；P2-4 安全相关 `.env` 说明保留）。

> 说明：本报告最初只做审计；随后已按报告完成一轮代码调整。原始证据保留，修复状态见 §1.1。
> 所有证据均为 `文件:行` 或可复现命令。

---

## 1.1 修复完成状态（2026-10-29 调整后）

| 编号 | 项 | 状态 | 处理方式 / 证据 |
| --- | --- | --- | --- |
| P0-1 | 前端 73 个 `.js` 产物 | ✅ 已完成 | 已全部删除；`npm test` / `npm run build` 通过 |
| P0-2 | `app/session/agent_executor.py` | ✅ 已完成 | 已删除 |
| P0-3 | `app/filesystem/search.py` | ✅ 已完成（连带 `filesystem.py`） | 已删除；原判定漏算 `filesystem.py:20` 的 `from . import search`，故连带删除唯一调用方 `app/filesystem/filesystem.py` 并清理 `__init__` 导出 |
| P0-4 | `WeatherAlert.vue` + 样式 | ✅ 已完成 | 已删除组件与 `weatherAlert.css` |
| P0-5 | `MobilePlugins.vue` | ✅ 已完成（改为接线） | 已接入 `MobileShell` 菜单与 `mobileViews` |
| P1-1 | `/plugins` 链路断开 | ✅ 已完成 | 路由已挂载；`/api/plugins` 直返 200（无尾斜杠 307） |
| P1-2 | `summarization_*` 只进管理 API | ✅ 已完成 | 压缩节点每次调用解析 `summarization_model` / `keep_messages`，API 改完即时生效，无需重启 |
| P1-3 | `compress_model` 取值被忽略 | ✅ 已完成 | 直连与代理模式均使用 `model_override`；`get_compress_model(model=...)` + 代理分支已接线 |
| P1-4 | 记忆持久化未接线 | ✅ 已完成 | `app/memory_store.py`（SqliteStore）已注入；`MemoryManager` 改为显式路径，避免 JSON 覆盖 SQLite；召回增加 query 重排 |
| P1-5 | Langfuse / trace 未接线 | ✅ 已完成 | 新增 `configure_langfuse()` 与 `/api/monitor/traces`；已安装 `langfuse 2.60.10`，回调注册验证通过 |
| P1-6a | `quota.py` 工具配额 | ✅ 已完成 | 执行前按参数预留 token、执行后结算差额；审批通过后才消耗配额；单次超支会返回配额错误 |
| P1-6b | `tenant_quota.py` / `ab.py` | ⚠ 保留并标注 | 仍为 M5 预留；顶部已标注“预留未接线” |
| P2-1 | `approval.py` 占位说明 | ✅ 已完成 | docstring 改为“已接入 /api/admin/approvals” |
| P2-2 | AGENTS 插件说明过期 | ✅ 已完成 | 改为“插件仍存在，按 `.enabled` 启用，`/api/plugins` 已挂载” |
| P2-3 | `router.py` 压缩/`.env` 注释 | ✅ 已完成 | 压缩注释改为实际行为；`.env` 改为 `config.json` |
| P2-4 | 代码注释残留 `.env` | ✅ 已完成（安全说明保留） | 配置相关注释与 `docs/changed_files.md`、`docs/codeact-sandbox-design.md` 已改；`.env*` 安全保护说明有意保留 |

**修复后链路状态**

| # | 链路 | 状态 |
| --- | --- | --- |
| 1 | 配置 → Settings → 行为 | ✅ P1-2/3/4 已接线 |
| 2 | 前端 → HTTP/SSE → FastAPI | ✅ `/api/plugins` 已挂载 |
| 3 | 一轮对话 → graph | ✅ |
| 4 | 工具循环 → 执行 | ✅ |
| 5 | 模型切换 / 压缩 / 持久化 | ✅ 压缩模型取值已生效 |
| 6 | 记忆 → 召回/存储 → 持久化 | ✅ SQLite Store |
| 7 | 可观测性 | ✅ chainlog/budget/trace 通过；Langfuse 回调已注册 |

### 1.2 代码审查修复（第二轮）

**标准轴**
- JSONC 注释/尾逗号改为单次扫描器（`config.py`），去掉重复状态机。
- 配额限制改为 `QuotaLimits` 类型，不再用位置二元组。
- 配额检查/执行/结算收敛到 `_execute_with_quota()`；审批通过后才消耗配额。
- `configure_langfuse()` 复用 `_langfuse_available()`，去掉重复探测。
- `main_graph.py` 统一 `settings` 导入；`memory_store` 测试重置的 docstring 改为“只释放连接”。

**规格轴**
- `summarization_keep_messages` 不再依赖 `summarization_model`；压缩节点每次调用读取配置，`/api/config/summarization` 修改即时生效。
- 配额执行前按参数预留 token、执行后结算差额；单次超支返回配额错误。
- `memory_persist_path` 只给 SqliteStore 使用；`MemoryManager` 改为显式路径，避免 JSON 覆盖数据库；召回增加 query 字符重合度重排。
- `/api/plugins` 增加无尾斜杠路由，前端改为直接请求 `BASE`，不再 307。
- `docs/changed_files.md`、`docs/codeact-sandbox-design.md` 中过期的 `.env.example` 引用已更新。

---

## 1. 全链路结论表（审计时快照）

| # | 链路 | 入口 | 出口 | 结论 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 1 | 配置 → Settings → 行为 | `config.example.json` / OS env | `Settings` 字段 | **有断点** | 见 P1-2/3/4/5 |
| 2 | 前端 → HTTP/SSE → FastAPI | `frontend/src/api/*` | `app/api/*` | **有断点** | `/api/plugins` 未挂载，见 P1-1 |
| 3 | 一轮对话 → graph | `deps.run/stream` | `filter→…→memory_store` | 通过 | `graph/main_graph.py:176-238` 11 个节点均接线 |
| 4 | 工具循环 → 执行 | `run_tool_loop` | Adapter→Registry→Permission→Executor | 通过 | `graph/nodes/base.py`、`harness/tool_adapter.py`、`execution/permission.py` |
| 5 | 模型切换 / 压缩 / 持久化 | `deps.switch_model` | `SwitchService` / `compress_messages` | **部分断点** | 切换/压缩已接；`compress_model` 取值未生效（P1-3） |
| 6 | 记忆 → 召回/存储 → 持久化 | `memory_recall` | `memory_store` | **有断点** | 图用 `InMemoryStore`；`MemoryManager` 生产未实例化（P1-4） |
| 7 | 可观测性 | chainlog / trace_span / budget | 落库 / 内存 | **部分断点** | chainlog、budget、trace_span 已用；Langfuse handler 未接（P1-5） |

---

## 2. P0 死代码

### P0-1 前端 73 个 `.js` 重复产物

- **现象**：`frontend/src` 下有 73 个被 git 跟踪的 `.js`，全部存在同名 `.ts` / `.vue`。
- **证据**：
  - `frontend/vite.config.ts:6-10` 明确注释“历史误编译的同名 .js 与 .ts 并存”，并强制 `extensions: ['.mts','.ts',...]` 把 `.ts` 提到 `.js` 前。
  - 全库没有显式 `from '.../x.js'` 的导入。
  - 例：`frontend/src/api/multiAgent.ts` 与 `multiAgent.js` 并存；`ChatInput.vue` 与 `ChatInput.vue.js` 并存。
- **影响**：当前构建不使用它们，但会误导搜索、增加仓库体积，并已出现“源码改了、`.js` 仍是旧逻辑”的漂移（见 P2-4）。
- **建议**：确认 `npm run build` / `npm test` 通过后，删除全部 73 个 `.js`；保留 `vite.config.ts` 的 `extensions` 配置作为防回归。

### P0-2 `backend/app/session/agent_executor.py`

- **现象**：全库 0 引用（含测试）。
- **证据**：文件内自带 `classify_error` / `PartBridgeQueue`（`agent_executor.py:18,69`）；实际链路使用 `harness.errors.classify_error` 与 `graph/events.py`。
- **影响**：重复实现 + 死代码，容易被误认为当前事件桥。
- **建议**：删除文件；若仍需 `PartBridgeQueue`，先确认无历史调用方再删。

### P0-3 `backend/app/filesystem/search.py`

- **现象**：全库 0 引用；`app/filesystem/__init__.py` 也未导出。
- **证据**：文件定义 `FileSystemSearch.glob/grep`（`search.py:85,161`）；实际工具走 `execution/tools` + `app/filesystem/ripgrep.py`。
- **影响**：死代码；与真实搜索实现重复。
- **建议**：删除文件，或明确作为未来接口并在 `__init__` 导出、补调用方。
- **更正（2026-10-29 第二轮）**：原"全库 0 引用"有误——`app/filesystem/filesystem.py:20`
  存在 `from . import search as _search`。仅删 `search.py` 会让 `import app.filesystem`
  直接失败（`app/runtime.py:122` 在启动期触发）。由于 `FileSystem` 类全库无实例化、
  无测试，处理为连带删除 `search.py` + `filesystem.py`，并从 `__init__.py` 移除
  `Entry` / `FileSystem` / `LocationResolvingError` / `Match` 导出。
  回归防护：`tests/unit/test_app_imports.py`。

### P0-4 `frontend/src/components/WeatherAlert.vue`

- **现象**：无任何组件/路由导入。
- **证据**：全库仅自身与 `.js` 产物引用；调用 `/api/plugins/weather-alert/call/tool_get_typhoon_info`（`WeatherAlert.vue:241`），该后端路由未挂载（见 P1-1）。
- **影响**：死组件 + 失效 API 调用。
- **建议**：删除组件及其专属样式；若保留天气预警功能，先恢复后端插件路由与页面入口。

### P0-5 `frontend/src/mobile/views/MobilePlugins.vue`

- **现象**：`MobileShell.vue:10-16` 的异步组件与 `mobileViews` 映射中均无它。
- **证据**：`MobileShell.vue:10-16`、`mobileViews` 表（`MobileShell.vue:55-62`）。
- **影响**：死页面。
- **建议**：删除；若移动端要管理插件，先修复 P1-1 再接入。

---

## 3. P1 未接线

### P1-1 `/plugins` 页面链路断开

- **现象**：前端有 `/plugins` 路由与页面，后端有插件路由文件，但 **router 从未挂载**。
- **证据**：
  - 前端：`frontend/src/router/index.ts:34-36` → `PluginsView.vue`；`frontend/src/api/plugins.ts:5` → `BASE = '/api/plugins'`。
  - 后端：`app/main.py:37-65` 的 `_include_app_routers` 列表没有 `plugins`；`app/api/plugins.py` 未被任何 `include_router` 引用。
  - 实际请求 `/api/plugins` 会 404。
- **影响**：插件管理页整体不可用；而 `backend/plugins` 下已有 11 个 `.enabled` 插件在运行，用户无法通过 UI 启停。
- **建议（三选一）**：
  1. **接线**：在 `_include_app_routers` 加 `plugins.router`（并补鉴权/测试）。
  2. **删除**：移除前端 `/plugins` 路由、`PluginsView`、`api/plugins.ts`、`stores/plugins.ts`，明确插件只能手动管理。
  3. **标注**：保留代码但在 README/AGENTS 写明“插件页未启用”。

### P1-2 `summarization_model` / `summarization_keep_messages` 只进管理 API

- **现象**：两个参数只被 `/api/config/summarization` 读写，压缩链路完全不读；前端也没有调用该 API。
- **证据**：
  - `app/api/config.py:36-60` 读写这两个字段。
  - `graph/main_graph.py:132-143` 构造压缩模型只用 `compress_model_enabled` / `compress_max_tokens`。
  - 前端全库无 `/api/config/summarization` 调用。
- **影响**：改这两个参数不会改变摘要/压缩行为。
- **建议**：接线到压缩节点，或删除字段与 API，或保留但把注释改成“仅管理 API 回显”。

### P1-3 `compress_model` 取值被忽略

- **现象**：只要非空就返回 `TierModel(tier="auto")`，配置的模型名没有传给路由。
- **证据**：`models/router.py:87-99`；运行时探针：
  - 设 `compress_model="deepseek/deepseek-v4-pro"` → endpoint model 仍是 `deepseek/deepseek-v4-flash`
  - 设 `compress_model="ollama_chat/qwen2.5:3b"` → endpoint model 仍是 `deepseek/deepseek-v4-flash`
- **影响**：用户以为切了压缩模型，实际仍走默认模型；成本隔离失效。
- **建议**：接线为真正的模型选择（请求级覆盖或独立 `TierModel`），或删除该参数并在示例注释中说明“仅开关”。

### P1-4 记忆持久化未接线

- **现象**：`memory_persist_path` 只被 `MemoryManager` 读取，但生产代码从未实例化 `MemoryManager`；图默认使用 `InMemoryStore`，重启即丢。
- **证据**：
  - `graph/main_graph.py:242`：`store=store if store is not None else InMemoryStore()`。
  - `app/deps.py:1569-1572` 调 `build_main_graph` 时没有传 `store`。
  - `cognition/memory.py:74` 是唯一读取 `memory_persist_path` 的地方；全库无 `MemoryManager(...)` 生产调用。
  - 运行时探针：构建后的图 `store` 类型为 `InMemoryStore`。
- **影响**：`memory_persist_path` 实际无效；长期记忆不跨重启。
- **建议**：把 `store` 接到 `MemoryManager`/持久化 Store，或删除该参数并在示例注释标注“当前仅内存记忆”。

### P1-5 Langfuse 未接线；本地 span 只写不读

- **现象**：`get_trace_handler` 与 `recent_spans` 全库无调用；`trace_span` 只把 span 追加到内存列表，从未被读取/导出。
- **证据**：
  - `observability/tracing.py:60`（handler）、`:90`（recent_spans）无调用方。
  - `app/main.py:74-76` 只把 `langfuse_*` 同步进环境变量，没有把 `CallbackHandler` 挂到 LLM/graph。
  - `graph/nodes/base.py:339,527,712` 使用了 `trace_span`，但 span 不落盘、不出 API。
- **影响**：配置 `langfuse_*` 不会产生 Langfuse 链路；本地 trace 也没有出口。
- **建议**：在模型调用处挂 `get_trace_handler`，或提供 `/api/monitor/traces` 读取 `recent_spans`；否则删除 `langfuse_*` 参数与 handler。

### P1-6 M4/M5 预留模块

- **`backend/execution/quota.py`（`ToolRegistryQuota`）**：✅ 已接线。
  - `graph/nodes/base.py:_execute_tool_call` 执行工具前 `get_quota(registry).allow()`，
    执行后按输出 token `consume()`；`token_budget`/`rate_limit` 默认 0（不限），
    只有显式声明限制的工具才受限。
  - 回归：`tests/unit/test_tool_quota_wiring.py`。
- **`backend/execution/tenant_quota.py`（`TenantQuota`）/ `backend/models/ab.py`**：⚠ 仍为 M5 预留。
  - 生产代码 0 引用；仅测试引用；顶部已标注“预留未接线”。
  - 接线需要先确定租户身份来源与 A/B 分流维度。

---

## 4. P2 文档/行为不一致

### P2-1 `execution/approval.py` 仍写“生产加固占位”

- **证据**：`approval.py:1-4` 写“M4 生产加固占位 / 实际审批流 M4 后续接入”；实际 `ApprovalQueue` 已被 `app/deps.py:1596`、`app/api/admin.py:90,109` 使用。
- **建议**：更新 docstring 为“内存审批队列，已接入 /api/admin/approvals”。

### P2-2 AGENTS.md 说内置插件已移除，但插件仍在运行

- **证据**：`AGENTS.md:210` 称“内置插件（http-client / internet-search / weather / 文档生成器等）已移除”；实际 `backend/plugins` 下有 11 个 `.enabled`，`sync_plugin_tools` 会把它们注册进 `ToolRegistry`（`app/deps.py:1557`）。
- **影响**：文档与运行时能力不一致，容易误判工具来源。
- **建议**：改文档为“插件仍存在，默认按 `.enabled` 启用；内置工具与插件工具并存”。

### P2-3 `models/router.py` 压缩模型注释过期

- **证据**：`router.py:87-96` 写“独立 model_name 隔离成本 / COMPRESS_MODEL 环境变量覆盖”，但代码只做真值判断（P1-3）；同时多处仍写 `.env`（`:234,255,258,259,277,280,281`）。
- **建议**：与 P1-3 一起修；至少把 `.env` 改为 `config.json`。

### P2-4 代码注释仍大量引用 `.env`

- **证据**：如 `models/catalog.py:131,612,1049`、`models/router.py:234,255`、`models/litellm_vision_patch.py:19` 等。
- **影响**：配置已迁移到 `config.json`，旧注释会误导排查。
- **建议**：批量把“`.env` 凭证/配置”改为“`config.json` 或环境变量”；安全保护相关 `.env` 字样保留。

---

## 5. 附录 A：115 项配置全量矩阵（审计时快照）

> 状态说明：**已接线** = 生产代码有读取点；**测试专用** = 仅 pytest 使用；**动态读取** = 通过 `getattr(settings, name)` 间接读取。
>
> 修复后新增 2 项：`tool_default_rate_limit` / `tool_default_token_budget`（默认 0 = 不限），见 P1-6a；因此当前 `config.example.json` 共 117 项。

| 参数 | 默认值 | 状态 | 证据 |
| --- | --- | --- | --- |
| `llm_model` | `"deepseek/deepseek-v4-flash"` | 已接线 | models/catalog.py |
| `llm_api_key` | `null` | 已接线 | models/catalog.py |
| `llm_api_base` | `"https://api.deepseek.com"` | 已接线 | models/catalog.py |
| `db_type` | `"sqlite"` | 已接线 | storage/backends.py |
| `db_url` | `null` | 已接线 | storage/backends.py |
| `db_host` | `"127.0.0.1"` | 已接线 | storage/backends.py |
| `db_port` | `0` | 已接线 | storage/backends.py |
| `db_username` | `"root"` | 已接线 | storage/backends.py |
| `db_password` | `""` | 已接线 | storage/backends.py |
| `db_name` | `"agentsuper"` | 已接线 | storage/backends.py |
| `session_db_path` | `"session.db"` | 已接线（统一数据根；含 auth_users） | app/session/db.py |
| `checkpoint_db_path` | `"checkpoints.db"` | 已接线（统一数据根） | graph/checkpoint.py |
| `checkpoint_persist` | `true` | 已接线 | graph/checkpoint.py |
| `postgres_checkpoint_url` | `""` | 已接线 | graph/checkpoint.py |
| `database_url` | `""` | 已接线 | graph/checkpoint.py |
| `vector_store_path` | `"vector_store"` | 已接线（统一数据根） | app/runtime.py |
| `chunk_size` | `500` | 已接线 | app/runtime.py |
| `chunk_overlap` | `200` | 已接线 | app/runtime.py |
| `kb_department` | `"default"` | 已接线 | app/runtime.py |
| `embedding_model` | `"BAAI/bge-small-zh-v1.5"` | 已接线 | app/runtime.py |
| `enable_reranker` | `true` | 已接线 | app/runtime.py |
| `reranker_model` | `"cross-encoder/ms-marco-MiniLM-L-6-v2"` | 已接线 | app/runtime.py |
| `model_download_timeout` | `600` | 已接线 | utils/model_download.py |
| `model_download_retries` | `2` | 已接线 | utils/model_download.py |
| `plugins_dir` | `"plugins"` | 已接线 | app/runtime.py |
| `summarization_model` | `null` | 已接线（压缩模型回退） | graph/main_graph.py |
| `summarization_keep_messages` | `20` | 已接线（压缩保留条数） | graph/main_graph.py |
| `compress_model` | `null` | 已接线（model_override） | models/router.py |
| `compress_model_enabled` | `true` | 已接线 | graph/main_graph.py |
| `compress_max_tokens` | `800` | 已接线 | graph/main_graph.py |
| `plan_model_enabled` | `true` | 已接线 | graph/main_graph.py |
| `plan_parallel_enabled` | `false` | 已接线 | graph/routers.py |
| `plan_max_fanout_calls` | `2` | 已接线 | graph/routers.py |
| `plan_fanout_min_budget` | `0.2` | 已接线 | graph/routers.py |
| `session_budget` | `0.0` | 已接线 | app/deps.py |
| `voice_tts_enabled` | `false` | 已接线 | app/runtime.py |
| `voice_tts_speaker` | `"Vivian"` | 已接线 | app/services/voice.py |
| `voice_tts_model_size` | `"0.6B"` | 已接线 | app/services/voice.py |
| `voice_tts_timeout` | `600` | 已接线 | app/services/voice.py |
| `llm_max_tokens` | `32000` | 已接线 | models/router.py |
| `token_estimate_correction` | `1.13` | 已接线 | cognition/token_counter.py |
| `context_tail_turns` | `2` | 已接线 | app/session/service.py |
| `compaction_tail_messages` | `0` | 已接线 | app/session/service.py |
| `tool_output_max_tokens` | `8000` | 已接线 | cognition/tool_output.py |
| `image_thumbnail_px` | `256` | 已接线 | app/image_processor.py |
| `image_use_ocr` | `false` | 已接线 | app/image_processor.py |
| `tesseract_cmd` | `""` | 已接线 | app/image_processor.py |
| `tessdata_dir` | `""` | 已接线 | app/image_processor.py |
| `ocr_languages` | `"chi_sim+eng"` | 已接线 | app/image_processor.py |
| `image_vlm_caption` | `true` | 已接线 | app/image_processor.py |
| `image_caption_model` | `"openai/gpt-4o-mini"` | 已接线 | app/image_processor.py |
| `image_caption_api_base` | `""` | 已接线 | app/image_processor.py |
| `image_caption_api_key` | `""` | 已接线 | app/image_processor.py |
| `image_caption_prompt` | `""` | 已接线 | app/image_processor.py |
| `image_caption_timeout` | `15` | 已接线 | app/image_processor.py |
| `admin_token` | `null` | 已接线 | app/api/deps.py |
| `cors_origins` | `["http://localhost:5173", "http://127...` | 已接线 | app/main.py |
| `vector_store_auto_clear` | `false` | 已接线 | app/main.py |
| `vector_store_ttl_days` | `0` | 已接线 | app/main.py |
| `vector_store_cleanup_interval_hours` | `24` | 已接线 | app/main.py |
| `max_concurrent_agents` | `4` | 已接线 | app/deps.py |
| `max_queue_size` | `20` | 已接线 | app/deps.py |
| `permission_approval_timeout` | `180` | 已接线 | app/runtime.py |
| `allow_source_writes` | `false` | 已接线 | app/runtime.py |
| `deepseek_thinking` | `"disabled"` | 已接线 | models/catalog.py |
| `ollama_thinking` | `"disabled"` | 已接线 | models/catalog.py |
| `ollama_num_ctx` | `4096` | 已接线 | models/catalog.py |
| `ollama_context_guard` | `true` | 已接线 | models/context_guard.py |
| `ollama_host` | `"http://localhost:11434"` | 已接线 | models/catalog.py |
| `ollama_api_base` | `""` | 已接线 | models/catalog.py |
| `litellm_debug` | `true` | 已接线 | observability/litellm_debug.py |
| `litellm_proxy_url` | `""` | 已接线 | models/router.py |
| `litellm_proxy_key` | `"sk-litellm-proxy"` | 已接线 | models/router.py |
| `litellm_debug_log` | `""` | 已接线（空 = log 根） | observability/litellm_debug.py |
| `agentsuper_log_dir` | `""` | 已接线（空 = log 根） | observability/trace_log.py |
| `agentsuper_data` | `""` | 动态读取（空 = OS 用户目录） | storage/paths.py |
| `agentsuper_cache` | `""` | 动态读取（空 = 跟随数据根） | storage/paths.py |
| `agentsuper_config` | `""` | 动态读取（空 = 跟随数据根） | storage/paths.py |
| `agentsuper_state` | `""` | 动态读取（空 = 跟随数据根） | storage/paths.py |
| `agentsuper_log` | `""` | 动态读取（空 = 跟随数据根） | storage/paths.py |
| `agentsuper_bin` | `""` | 动态读取（空 = 跟随数据根） | storage/paths.py |
| `agentsuper_migrate_legacy` | `true` | 已接线（首次启动迁移） | storage/paths.py |
| `model_probe_timeout` | `4.0` | 已接线 | models/catalog.py |
| `deepseek_tokenizer_dir` | `""` | 已接线 | models/deepseek_tokenizer.py |
| `langfuse_public_key` | `""` | 已接线（litellm 回调） | observability/tracing.py |
| `langfuse_secret_key` | `""` | 已接线（litellm 回调） | observability/tracing.py |
| `langfuse_host` | `""` | 已接线（litellm 回调） | observability/tracing.py |
| `memory_persist_path` | `"agent_memory.db"` | 已接线（统一数据根） | app/memory_store.py |
| `auth_secret` | `null` | 已接线 | app/auth.py |
| `auth_token_ttl` | `2592000` | 已接线 | app/auth.py |
| `auth_users_path` | `"auth_users.json"` | 仅旧 JSON 一次性导入 | app/auth.py |
| `chain_log_enabled` | `true` | 已接线 | app/main.py |
| `chain_log_queue_size` | `5000` | 已接线 | observability/chainlog/store.py |
| `chain_log_flush_interval` | `1.0` | 已接线 | observability/chainlog/store.py |
| `chain_log_flush_batch` | `200` | 已接线 | observability/chainlog/store.py |
| `chain_log_retention_days` | `7` | 已接线 | observability/chainlog/store.py |
| `chain_log_max_rows` | `200000` | 已接线 | observability/chainlog/store.py |
| `chain_log_max_data_chars` | `4000` | 已接线 | observability/chainlog/store.py |
| `chain_log_tool_calls` | `true` | 已接线 | observability/chainlog/tracer.py |
| `tavily_api_key` | `""` | 已接线 | plugins/internet_search.py、execution/tools/web_tools.py |
| `bing_api_key` | `""` | 已接线 | plugins/internet_search.py、execution/tools/web_tools.py |
| `ssrf_allow_internal` | `false` | 已接线 | utils/ssrf.py |
| `harness_tool_mode` | `""` | 已接线 | harness/capabilities.py |
| `harness_native_models` | `[]` | 已接线 | harness/capabilities.py |
| `harness_fallback_models` | `[]` | 已接线 | harness/capabilities.py |
| `harness_real_test` | `false` | 测试专用 | tests/integration/test_harness_real_model.py |
| `harness_real_model` | `""` | 测试专用 | tests/integration/test_harness_real_model.py |
| `l4_model_switch` | `false` | 测试专用 | tests/integration/test_model_switch_l4_real_models.py |
| `l4_model_a` | `""` | 测试专用 | tests/integration/test_model_switch_l4_real_models.py |
| `l4_model_b` | `""` | 测试专用 | tests/integration/test_model_switch_l4_real_models.py |
| `l4_model_pairs` | `""` | 测试专用 | tests/integration/test_model_switch_l4_real_models.py |
| `harness_smoke_model_a` | `""` | 测试专用 | tests/e2e、integration、perf |
| `harness_smoke_model_b` | `""` | 测试专用 | tests/integration、perf |
| `harness_ollama_test` | `false` | 测试专用 | tests/integration/test_harness_ollama_smoke.py |
| `harness_ollama_tools_model` | `"ollama/qwen2.5:3b"` | 测试专用 | tests/integration/test_harness_ollama_smoke.py |
| `harness_ollama_text_model` | `"ollama/deepseek-r1:latest"` | 测试专用 | tests/integration/test_harness_ollama_smoke.py |

---

## 6. 附录 B：测试专用参数（非死代码）

以下 11 项在生产代码中 0 引用，只由 pytest 读取，**不要按死参数删除**：

- `harness_real_test`、`harness_real_model`
- `l4_model_switch`、`l4_model_a`、`l4_model_b`、`l4_model_pairs`
- `harness_smoke_model_a`、`harness_smoke_model_b`
- `harness_ollama_test`、`harness_ollama_tools_model`、`harness_ollama_text_model`

证据：`tests/integration/test_harness_real_model.py`、`test_model_switch_l4_real_models.py`、`test_harness_ollama_smoke.py`、`tests/e2e/test_harness_golden_live.py`、`tests/perf/test_harness_perf.py`。

---

## 7. 附录 C：前端 ↔ 后端 API 覆盖

### 断开的调用（前端调了，后端没有可命中的挂载路由）

| 前端调用 | 后端现状 | 结论 |
| --- | --- | --- |
| `/api/plugins`（`api/plugins.ts:5`） | `app/api/plugins.py` 存在但未 `include_router` | **404，P1-1** |
| `/api/plugins/weather-alert/call/tool_get_typhoon_info`（`WeatherAlert.vue:241`） | 同上；且组件无导入 | 死组件 + 404 |
| `/api/plugins/weather-alert/status`（仅 `SettingsPanel.vue.js`） | 同上；源码 `.vue` 无此调用 | 仅残留产物 |

### 后端有、前端无调用（约等于运维/外部接口，不计问题）

- `/api/admin/*`（tools/agents/prompts/metrics/approvals/health）
- `/api/auth/register`、`/api/auth/token`（旧账号接口）
- `/api/config/summarization`（P1-2 已单列）

> 说明：按“前端字面前缀”近似匹配；`/api/monitor/*` 是 `app/main.py` 直接注册的平台路由，不在 `app/api` 静态扫描范围内，属于正常。

---

## 8. 附录 D：排除的假阳性

| 候选 | 为什么不是死代码 |
| --- | --- |
| `agentsuper_data/cache/config/state/log/bin` | `storage/paths.py` 通过 `getattr(settings, name)` / `data_path()` 动态读取；子目录默认跟随数据根 |
| `backend/plugins/*.py` | `PluginLoader` 扫描目录并按 `.enabled` 加载；当前 11 个启用 |
| `backend/ttsclone/*.py` | 由 `app/services/voice.py` 作为子进程调用 |
| `backend/storage/schema.py` | 通过 `from storage import backends, schema as storage_schema` 分组导入 |
| `frontend/src/components/MultiAgentResponse.vue` | 仅测试引用；AGENTS 明确“保留给旧测试与回滚，验收通过前不要删” |
| 测试专用参数 | 见附录 B |
| `POST /api/chat` / `/api/chat/stream` 等 | 已由平台 `chat.router` 挂载（`app/main.py:363-368`） |

---

## 9. 附录 E：验证方法

1. **配置引用扫描**：对 115 个 key 扫描 `settings.<key>`、`getattr(settings, "<key>")`，区分 app / tests。
2. **AST 空实现扫描**：对 `app/graph/harness/models/cognition/execution/observability/storage/utils/plugins/extensions` 扫描仅 `pass` / 空函数体，结果 0。
3. **压缩模型探针**：
   ```text
   compress_model="deepseek/deepseek-v4-pro" -> tier=auto, endpoint=deepseek/deepseek-v4-flash
   compress_model="ollama_chat/qwen2.5:3b"   -> tier=auto, endpoint=deepseek/deepseek-v4-flash
   ```
4. **图 store 探针**：`build_main_graph(...)` 后 `type(graph.store).__name__ == "InMemoryStore"`。
5. **路由静态提取**：解析 `@router.<method>` + `APIRouter(prefix=...)` + `_include_app_routers`；确认 `plugins.router` 无挂载。
6. **前端产物核对**：73 个 `.js` 均有同名 `.ts/.vue`；无显式 `.js` 导入；`vite.config.ts` 已强制 `.ts` 优先。

---

## 10. 建议处理顺序

1. **先修 P1-1**（插件页 404）：决定“接线”还是“删页面”，避免用户继续点进坏页面。
2. **再定 P1-2/3/4/5**：这四项都是“参数看起来能用、实际不生效”，建议统一选择“接线”或“删除并在示例注释标注未实现”。
3. **清 P0**：73 个 `.js`、两个后端死模块、两个死前端组件；删除前跑 `npm run build` / `npm test` / `pytest`。
4. **最后修 P2**：把 `approval.py`、`AGENTS.md`、`router.py` 和 `.env` 残留注释改准确。
