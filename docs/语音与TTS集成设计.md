# 语音与 TTS 集成设计（Agent 主线注册 · 无 HTTP 直连）

| 项 | 内容 |
|---|---|
| 版本 | v0.2 |
| 日期 | 2026-08-30 |
| 状态 | ✅ 已按方案 B 实现（V1 录入兼容 + V2 朗读 + V3 主线注册） |
| 关联 | `docs/优化行动计划.md` 4.4 |

---

## 一、背景与目标

前端已具备「语音输入麦克风 + AI 消息朗读」雏形，但当前链路**直连 ttsclone HTTP 服务**（`localhost:7861`，经 vite proxy `/tts-api`）。目标：

1. **TTS/语音作为「主线 Agent 功能」注册**进主 RAGAgent 工具集（模型可主动合成语音/转写音频），而非仅靠插件按需挂载。
2. **消除对 ttsclone HTTP 服务的直接接口调用**：统一改为**后端 subprocess 驱动**（复用 `voice_clone.py` 的子进程架构，进程级隔离、零语音依赖进后端 venv）。
3. 前端录制/朗读链路改走后端 `/api/voice/*`，**不直连 7861**。
4. 完整**复用主线公共能力**（上下文压缩、工具输出截断、权限、异步执行、降级语义）。

---

## 二、现状（代码事实）

| 层 | 现状 | 问题 |
|---|---|---|
| 主线 Agent | `voice_clone.py` 插件已存在（`tool_custom_voice`/`tool_voice_clone`/`tool_voice_design`/`tool_voice_transcribe`，subprocess 调 `ttsclone/clone.py`）；意图挂载已就绪（`graphmod/base.py:321` 语音关键词 → `plugin_voice-clone_`） | 依赖 `backend/plugins/voice_clone.py.enabled` 文件；作为**插件**存在而非主线一等能力；无 `VOICE_TTS_*` 配置门控 |
| 前端 | `ChatInput.vue` 麦克风（Web Speech API → MediaRecorder 降级）；`MultiAgentView.vue` AI 消息朗读按钮；`api/voice.ts` | `api/voice.ts` 走 `/tts-api`（vite proxy → **7861 直连**）——即「接口调用」 |
| ttsclone | `ttsclone/server.py`（Qwen3-TTS + Whisper，7861）；`ttsclone/clone.py`（CLI：custom/design/clone/transcribe） | HTTP 服务需独立启动、暴露端口；与后端鉴权/CORS/统一信封无关 |

---

## 三、方案对比与选定

| 方案 | 思路 | 优点 | 缺点 | 取舍 |
|---|---|---|---|---|
| A · 保持 HTTP 直连 7861 | 前端/vite proxy 直调 ttsclone | 已有、改动小 | 独立服务依赖、无鉴权无统一信封、非主线注册 | ❌ 否（正是要消除的「接口调用」） |
| B · 后端代理 + 子进程（**选定**） | 后端 `app/services/voice.py` subprocess 驱动 `clone.py`；`/api/voice/*` 与主 Agent 工具共用同一 service；前端只调 `/api/voice/*` | 无外部 HTTP 依赖；进程隔离；统一信封/鉴权/CORS；**同一实现同时服务 API 与主线工具**；与现有 `voice_clone.py` 架构同源 | 需后端 venv 有 subprocess 调用权（本就允许，`voice_clone.py` 已在用）；模型下载/推理较慢 | ✅ **采用** |
| C · 纯浏览器 TTS | `speechSynthesis` 朗读 + Web Speech 输入 | 零后端 | 音质差、跨浏览器不一致、无克隆/音色 | 作为**降级兜底**保留（不替代） |

---

## 四、架构设计

### 4.1 后端语音 Service（`app/services/voice.py`，子进程，无 HTTP 调用）

- 复用 `voice_clone.py` 的 subprocess 模式，抽成 service 层（**唯一实现**，API 与主线工具共用，避免双路径漂移）：
  - `voice_enabled()`：`VOICE_TTS_ENABLED` + `ttsclone` 目录/`clone.py` 存在性探测。
  - `available_speakers() / available_languages()`：静态表（同 `voice_clone.py` 常量）。
  - `synthesize(text, speaker, language, instruct, out_path) -> path`：`clone.py custom <text> --speaker … --output <out>`，输出写 `backend/data/generated/`。
  - `transcribe(audio_bytes, suffix) -> text`：临时文件 → `clone.py transcribe <audio>` → 解析 JSON/文本取 `text`。
  - `_run(args, timeout)`：`subprocess.run(capture_output, cwd=ttsclone)`；解析末行 JSON；超时/缺 python/缺目录 → 返回结构化错误。
- **异步隔离**：阻塞 `subprocess.run` 一律经 `asyncio.to_thread`（或 `loop.run_in_executor`）执行——主线工具执行在 SelectorEventLoop，直接阻塞会卡死整个事件循环（对齐 `tool_execute` 的线程桥设计）。

### 4.2 API 路由（`app/api/voice.py`，注册 `prefix="/api/voice"`）

| 端点 | 入参 | 出参 | 用途 |
|---|---|---|---|
| `GET /api/voice/status` | — | `{enabled, speakers, languages, device}` | 前端探测可用性 |
| `POST /api/voice/transcribe` | multipart `audio` | 统一信封 `{code:0, data:{text}}` | 前端录音转写 |
| `POST /api/voice/tts` | form `text/speaker/language/instruct` | `FileResponse(audio/wav)` 或统一错误信封 | 前端朗读 |

- 全部走 `app/api/responses.py` 统一信封（`ok()`/`fail()`/`ApiError`），受 `AuthMiddleware` 与 CORS 保护。
- ttsclone 缺失/禁用 → `status.enabled=false`；transcribe/tts 返回 503 统一错误（**不崩会话**）。

### 4.3 主 Agent 工具注册（主线一等能力）

- 对齐 **memory 工具注入机制**（`graphmod/base.py` `_build_tools`，`memory is not None` 时条件注册）：新增构造参数 `voice_service=None`（`runtime.py` 按 `VOICE_TTS_ENABLED` 注入），非 None 时注册：
  - `tool_tts_synthesize(text, speaker='Vivian', language='Auto', instruct='', output_path='')` → 合成 wav 落 `data/generated/`，返回路径+简短说明（模型可答「已生成语音文件 xxx.wav」）。
  - `tool_voice_transcribe(audio_path)` → 本地/会话音频转文本（模型处理用户上传的音频附件时可用）。
  - （可选）`tool_voice_design(text, voice_description)` —— 1.7B 专属，作为 v1 预留。
- **意图挂载保留**（token 优化）：`_INTENT_RULES` 语音关键词已存在（`base.py:321`），新工具名前缀 `tool_tts_synthesize`/`tool_voice_transcribe` 追加到该组；`_CORE_TOOL_PREFIXES` 不变（核心文件工具常驻，语音按意图挂载省 token，与插件/generator 同一策略）。
- **系统提示词**：`app/agent/tools.py` 新增「语音」段（对齐「新增插件工具必须更新 system prompt」约定）：说明 `tool_tts_synthesize` 用于把答复内容生成可下载语音、`tool_voice_transcribe` 用于转写音频；文件落 `data/generated/`。
- **不在 plugins/ 注册**：语音作为 service 注入的一等工具，不依赖 `voice_clone.py.enabled` 文件（插件保留兼容，二者指向同一 service，避免双实现）。

### 4.4 前端接入（复用现有 UI + 降级链）

- `api/voice.ts` 由 `/tts-api` 改为 `/api/voice/*`（后端代理，**无 7861 直连**）；`ttsHealth` → `/api/voice/status`。
- vite proxy `/tts-api` 移除（不再需要）。
- 朗读/麦克风 UI 不变：`MultiAgentView` 朗读按钮、`ChatInput` 麦克风按钮；`speechSynthesis` 降级链保留（ttsclone/后端不可用时浏览器兜底）。

---

## 五、公共能力复用清单（对齐其他 Agent，重点）

| 公共能力 | 现有实现 | 语音如何复用 |
|---|---|---|
| 上下文压缩/预算 | `llm_call_budget` + `truncate_messages` + `_step_summarize` + `LongTaskCoordinator`（C5） | 语音工具**返回体极小**（路径/文本/错误），天然不占上下文；转写超长文本走 `bound_tool_output` 截断，不新增压缩路径 |
| 工具输出截断 | `bound_tool_output`（200 行/32KB，超限写 `data/truncation/` + 续读提示） | `tool_voice_transcribe` 长文本输出复用同一函数，超限落盘 + 提示 |
| 结果信封 | `file_tools.unwrap`（`{title, metadata, output}`） | 语音工具返回统一 envelope，消费点（`_bound_plugin_result`/`run_tool`）解包一致 |
| 权限模型 | `PermissionManager`（workspace = backend/ + worktree） | 输出写 `data/generated/`（workspace 内）→ 免外部审批，与 generator 插件一致；无 `NeedsPermission` 分支 |
| 异步执行 | `tool_execute` Popen+线程桥（SelectorEventLoop 兼容） | `subprocess.run` 一律 `asyncio.to_thread`，不阻塞事件循环 |
| 降级语义 | reranker/embedding 失败 → 链路降级不崩 | ttsclone 缺失 → 工具返回明确错误；`/api/voice/status` `enabled=false`；前端 speechSynthesis 兜底；三者同风格 |
| 意图挂载 | `_INTENT_RULES` 关键词 → 工具前缀（省 token） | 语音关键词已存在，仅追加新工具前缀 |
| 系统提示词 | `tools.py` 分工具段 | 追加「语音」说明段 |
| 热更新 | `refresh_tools()` asyncio.Lock | 语音工具随 `_build_tools` 注册，配置变更走 refresh 同路径 |

---

## 六、配置项（`backend/.env` + `frontend/.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `VOICE_TTS_ENABLED` | `false` | 主 Agent 语音工具与 `/api/voice` 总开关（默认关，避免无模型环境误挂） |
| `VOICE_TTS_SPEAKER` | `Vivian` | 默认音色（9 个预设之一） |
| `VOICE_TTS_MODEL_SIZE` | `1.7B` | `0.6B`（快）或 `1.7B`（好） |
| `VOICE_TTS_TIMEOUT` | `600` | 单次合成/转写超时（秒） |
| `VOICE_TTS_AUTO` | `false` | AI 回复结束是否自动合成语音（v1 不做，预留） |
| `VITE_TTS_BASE` | `/api/voice` | 前端语音基址（默认走后端，无外部直连） |
| `VITE_TTS_SPEAKER` | `Vivian` | 前端朗读默认音色 |

> **目录固定**：ttsclone 位于 `backend/ttsclone/`（不配置路径，service 直接定位）。
> **模型下载**：
> - **TTS 合成模型**：`VOICE_TTS_ENABLED=true` 时 runtime 后台线程调 `VoiceService.ensure_models()` 预下载（ModelScope 优先 / HF 回退，断点续传，复用 `app/utils/model_download.py`）到 `backend/ttsclone/models/`——不阻塞启动、失败仅降级（`/api/voice/*` 返回 503 并提示）。ttsclone 自身**不自动下载**（`clone.py` 启动下载已移除、`server.py` 改为缺失即报错）。
> - **Whisper（转写用）**：**不随启动下载**，并入安装/预下载步骤（`pip install -r requirements-voice.txt` 后 `python scripts/download_tts_model.py --whisper`）；缺失时 `clone.py` 转写返回明确错误，不会运行时静默拉取 1.6GB。
> - **图片 caption 模型**：`scripts/download_image_model.py`（默认 `ollama pull llava`；公网 API 模型无需下载）。
> `VoiceService.enabled` 仅校验 TTS 合成模型已就绪（`has_model`）；Whisper 缺失只影响转写（降级 503）。

---

## 七、测试与脚本

- **单元测试** `backend/tests/test_voice_service.py`：
  - monkeypatch `subprocess.run`：`custom` 输出解析、`transcribe` 文本解析、非零返回码、超时、缺 python/目录 → 结构化错误；
  - `voice_enabled()` 门控、speakers/languages 常量。
- **API 测试** `tests/test_api_voice.py`：`/api/voice/status`（enabled 变体）、`/api/voice/transcribe`（mock service 成功/失败）、`/api/voice/tts`（mock service → FileResponse wav）；统一信封/鉴权。
- **冒烟脚本** `scripts/smoke_voice_api.py`：无真实模型也能验证——未启用时 `status.enabled=false`、transcribe/tts 返回 503 明确错误；已启用（本机有 ttsclone 模型）时真实合成一段 wav + 转写，断言文件非空。
- **前端** `npm test` 现有 31 例保持绿；`api/voice.ts` 改指向后无类型/构建回归（`vue-tsc + vite build`）。

---

## 八、落地步骤（评审通过后）

1. `app/services/voice.py`（subprocess + `asyncio.to_thread` + 结构化错误）。
2. `app/api/voice.py` 三端点 + `main.py` 注册 + `config.py` 加 `VOICE_TTS_*`。
3. `runtime.py` 按开关构造 `voice_service` → 注入 RAGAgent（`graphmod/base.py` 条件注册工具 + 意图前缀 + `tools.py` 系统提示词段）。
4. 前端 `api/voice.ts` 改 `/api/voice/*`、移除 vite `/tts-api` proxy。
5. 测试 + 冒烟脚本 + 更新 `docs/优化行动计划.md` 4.4 状态。

## 九、风险与取舍

| 风险 | 缓解 |
|---|---|
| Qwen3-TTS 模型大/冷启动慢 | `VOICE_TTS_ENABLED=false` 默认关；`0.6B` 快模型；超时可配；失败降级 speechSynthesis |
| subprocess 阻塞事件循环 | 一律 `asyncio.to_thread`（对齐 tool_execute 线程桥） |
| 与 `voice_clone.py` 插件双实现漂移 | service 为唯一实现；插件改为薄封装指向 service（或标记废弃） |
| 语音文件堆积 `data/generated/` | 沿用生成文件清理逻辑（`cleanup_truncated`/TTL 同类，v1 不新增） |
