# 架构设计：图片→视频 与 文档→TTS 生成管线

> 目标：在现有 RAG + Agent 系统中新增两条生成能力——
> A. 图片 + 提示词 → 视频（image-to-video）
> B. 文档 + 提示词 → TTS 语音（doc-to-speech）
>
> 设计原则：复用现有插件 / 存储 / 会话模式；两条管线共享同一套"任务编排"骨架；长耗时任务全部异步化，不阻塞 FastAPI 事件循环。

---

## 1. 两条管线的共同骨架

两条管线本质是同一个模式：

```
资产(Asset) + 提示词(Prompt) ──► 解析/预处理 ──► 生成模型 ──► 产物(Output Asset)
```

| 维度 | 管线 A：图片→视频 | 管线 B：文档→TTS |
|------|------------------|------------------|
| 输入资产 | 图片（jpg/png/webp，或视频取首帧） | 文档（docx/pdf/txt/md） |
| 提示词作用 | 描述运动/运镜/风格 | 指定音色、情感、语速、风格 |
| 生成模型 | 图生视频模型（本地 CogVideoX/SVD 或云端 Kling/Runway/Sora） | TTS 模型（现有 voice_clone 子进程） |
| 产物 | mp4（+封面/缩略图） | mp3/wav（+可选 srt 字幕） |
| 耗时 | 分钟级 | 十秒~分钟级（长文档更久） |

**共同难点**：都是长耗时任务 → 必须异步化 + 任务状态机 + 进度上报；输入都可能超模型上限（视频时长、TTS 文本长度）→ 需要"切分→分段生成→拼接"。

---

## 2. 总体架构（分层）

```
┌────────────────────────────────────────────────────────────┐
│  接入层  FastAPI 路由                                        │
│  POST /api/media/tasks              创建任务 (202 + task_id)│
│  GET  /api/media/tasks/{id}         查状态/进度              │
│  GET  /api/media/tasks/{id}/events  SSE 实时进度             │
│  POST /api/media/tasks/{id}/cancel  取消                    │
│  GET  /api/generated/download/*     下载产物（已存在）        │
└───────────────┬────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────┐
│  编排层  MediaTaskOrchestrator（核心）                       │
│  · asyncio.Queue 任务队列（in-process，起步够用）             │
│  · 并发信号量 Semaphore(MAX_CONCURRENT_MEDIA_TASKS)         │
│  · 任务状态机 queued→running→succeeded/failed/cancelled     │
│  · 指数退避重试 / 超时控制 / 进度上报                         │
└───────┬───────────────────────────────┬────────────────────┘
        │                               │
┌───────▼──────────┐         ┌──────────▼─────────┐
│ Pipeline A       │         │ Pipeline B         │
│ 图片→视频         │         │ 文档→TTS           │
│ 1 图片预处理      │         │ 1 文档解析          │
│ 2 提示词构建      │         │ 2 文本清洗/分段     │
│ 3 分镜(可选)      │         │ 3 提示词→语音参数   │
│ 4 逐clip生成      │         │ 4 逐段合成          │
│ 5 ffmpeg拼接     │         │ 5 ffmpeg拼接+字幕   │
└───────┬──────────┘         └──────────┬─────────┘
        │                               │
┌───────▼───────────────────────────────▼─────────┐
│  模型网关 ModelGateway（统一接口）                 │
│  · 本地子进程：subprocess 隔离（复用 voice_clone  │
│    的 ttsclone 模式，新增 vidgen 目录）            │
│  · 云端 API：Kling / Runway / Sora / TTS 云服务   │
│  · 多后端路由（成本/质量/速度）+ 错误码归一化        │
└───────┬───────────────────────────────┬─────────┘
        │                               │
┌───────▼──────────┐         ┌──────────▼─────────┐
│ 产物存储          │         │ 任务元数据          │
│ data/generated/  │         │ SQLite media_tasks │
│ (复用 /api/generated)       │ (沿用 init_db 模式) │
└──────────────────┘         └────────────────────┘
```

---

## 3. 统一任务模型（核心抽象）

两条管线共用一个任务结构，是这套架构的"接口契约"：

```json
{
  "task_id": "uuid4",
  "type": "image_to_video | doc_to_tts",
  "status": "queued | running | succeeded | failed | cancelled",
  "stage": "preprocess | parse | segment | generate | merge | done",
  "input": {
    "asset_path": "data/uploads/xxx.png",
    "asset_type": "image | doc"
  },
  "prompt": "让画面中的云缓缓流动，镜头缓慢推近…",
  "params": {
    "duration": 5,
    "fps": 24,
    "voice": "Vivian",
    "instruct": "温柔舒缓，语速稍慢",
    "model": "local | kling | ..."
  },
  "progress": 0.0,
  "logs": [],
  "output": { "path": "data/generated/xxx.mp4", "url": "/api/generated/xxx.mp4" },
  "error": null,
  "retry_count": 0,
  "created_at": "…"
}
```

SQLite 表 `media_tasks`（在 `init_db()` 里追加建表，与 session.db 同模式）：

```sql
CREATE TABLE IF NOT EXISTS media_tasks (
  task_id     TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued',
  stage       TEXT,
  input_json  TEXT,
  prompt      TEXT,
  params_json TEXT,
  progress    REAL DEFAULT 0,
  logs_json   TEXT,
  output_json TEXT,
  error       TEXT,
  retry_count INTEGER DEFAULT 0,
  created_at  TEXT,
  updated_at  TEXT
);
```

> 为什么不用现有 `data/generated` 的 JSON 元数据？任务有生命周期（进度/重试/取消），SQLite 查询和恢复更方便，且与现有 init_db 基建一致。

---

## 4. Pipeline A：图片 + 提示词 → 视频

**步骤**：

1. **输入校验与预处理**：PIL 读取图片，校验格式/尺寸，超限则等比缩放（如 720p）；若用户传的是视频，抽首帧作为输入。
2. **提示词构建**：原始 prompt 直接传给模型；可选"提示词增强"——用现有 LiteLLM 网关把短 prompt 扩写成带运镜/光线/风格的完整描述（可配置开关）。
3. **分镜（可选）**：视频时长 > 单 clip 上限（如 5~10s）时，用 LLM 把 prompt 拆成 N 个分镜描述，每个分镜对应一个 clip。
4. **逐 clip 生成**：调用模型网关 `image_to_video(image, prompt, duration, fps)`，产出 5s 片段。
5. **拼接与封装**：ffmpeg concat 多 clip（可加淡入淡出转场）→ H.264 mp4；另生成封面 jpg + 缩略图。
6. **产物登记**：写入 `data/generated/`，任务 output 指向 `/api/generated/download/<file>`。

**关键参数**：`duration`、`fps`、`resolution`、`seed`（固定 seed 保证可复现）。

---

## 5. Pipeline B：文档 + 提示词 → TTS

**步骤**：

1. **文档解析**：docx（python-docx）/ pdf（pypdf，已有）/ txt / md → 提取结构化文本（保留章节标题层级）。
2. **文本清洗**：去空行、页码、页眉页脚；表格转自然语言文本。
3. **分段**：TTS 模型有单次文本长度上限（如 300 字/段），按段落→句子切分，每段 ≤ max_chars，段落边界优先，必要时按句号/问号断句。
4. **提示词 → 语音参数**：从 prompt 中解析出 `voice / instruct(情感/语速/风格) / language / model_size`。建议用 LLM 结构化输出（JSON）解析，规则解析做兜底。
5. **逐段合成**：每段调用现有 `voice_clone.py` 的 `tool_custom_voice` / `tool_voice_clone` / `tool_voice_design`（subprocess 隔离，环境零依赖）。
6. **拼接**：ffmpeg concat 所有 wav 片段 → 最终 mp3/wav；段间可加 300ms 静音间隔避免粘连。
7. **可选：字幕**：用 `tool_voice_transcribe`（Whisper）对最终音频生成 srt 字幕，便于与视频管线联动（图片配解说）。

**失败策略**：支持两种模式——`strict`（任一段失败整任务失败）/ `skip_bad`（失败段跳过，产物标注缺段），由 params 控制。

---

## 6. 插件设计（沿用 tool_* 模式）

新增两个插件文件，与现有 `plugins/voice_clone.py` 完全同构：

**`plugins/video_gen.py`**

```python
PLUGIN_NAME = "video-gen"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Image-to-video via isolated subprocess or cloud API"

def tool_image_to_video(image_path, prompt, duration=5, fps=24,
                        model="local", seed=0, timeout=1200) -> str:
    # 校验图片存在 → 组装参数 → _run_video(args) subprocess →
    # 返回 "Video generation success! Output: data/generated/xxx.mp4"
```

**`plugins/doc_tts.py`**

```python
PLUGIN_NAME = "doc-tts"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Document-to-speech: parse doc, split text, synthesize per segment"

def tool_doc_to_speech(doc_path, prompt, voice="Vivian", instruct="",
                       language="Auto", model_size="1.7B",
                       fail_mode="strict", timeout=900) -> str:
    # 解析 → 分段 → 逐段调用 TTS → ffmpeg 拼接 → 返回产物路径
```

**subprocess 隔离**：参照 `ttsclone/` 模式，新增独立目录 `vidgen/`（本地图生视频模型 + 自己的 .venv），AgentSuper 后端零模型依赖。模型网关统一封装：

```python
# app/services/model_gateway.py
class ModelGateway:
    def image_to_video(self, image_path, prompt, **params) -> dict: ...
    def text_to_speech(self, text, voice, instruct, **params) -> dict: ...
    # 内部: 按 model 参数路由到 local_subprocess / cloud_api
    # 错误码归一化: TIMEOUT / RATE_LIMIT / MODEL_ERROR / INPUT_INVALID / OK
```

---

## 7. 任务调度与并发

**起步方案（V1，单机）**：in-process 异步队列，与现有 agent_bus 事件循环模式一致。

```python
# app/services/media_orchestrator.py
class MediaTaskOrchestrator:
    def __init__(self, max_concurrent=2):
        self.queue = asyncio.Queue()
        self.sem = asyncio.Semaphore(max_concurrent)
        self.tasks: dict[str, MediaTask] = {}

    async def submit(self, task) -> str:      # 入队，返回 task_id
    async def _worker(self):                   # 循环取任务 → 执行 → 更新状态
    async def cancel(self, task_id) -> bool:   # 置 cancelled，worker 检查点退出
    async def stream_events(self, task_id):    # asyncio.Queue 按 task 推送进度 → SSE
```

- 在 `main.py` 的 `lifespan` 中 `create_task(orchestrator.start())`（与 agent_bus.start_all() 并列）。
- 超时：视频 1200s、TTS 900s（对齐现有 voice 600s timeout 的做法）。
- 重试：指数退避 `2^n` 秒，最多 2 次；仅对 `TIMEOUT / RATE_LIMIT` 重试，`INPUT_INVALID / MODEL_ERROR` 直接失败。

**演进方案（V3，分布式）**：换 Celery + Redis（或 ARQ），worker 独立进程部署在 GPU 机器上；任务表加 `worker_id` 支持断点续跑。

---

## 8. 存储与交付

| 类型 | 位置 | 复用 |
|------|------|------|
| 上传资产（图片/文档） | `data/uploads/` | 现有 FileStore（doc_id 元数据） |
| 生成产物（视频/音频） | `data/generated/` | 现有 `/api/generated` 列表/下载/删除，开箱即用 |
| 任务元数据 | SQLite `media_tasks` 表 | `init_db()` 追加建表 |
| 清理策略 | 后台定时任务，TTL 7 天 | 新写一个轻量 `media_cleaner` |

前端交付：产物 URL 直接走 `/api/generated/download/<filename>`，`<video>` / `<audio>` 标签可预览。

---

## 9. 错误处理与可观测性

- **日志**：沿用 `logging` + `RequestLogMiddleware` / `monitor.get_stats`；任务 `logs` 字段追加式记录每阶段耗时。
- **失败现场**：subprocess 失败时把 stderr 末尾 N 行写入 `error` 字段（对齐 voice_clone 的 json 解析模式）。
- **进度**：`progress` 按阶段加权（解析 10% → 分段 20% → 生成 20%~90% → 拼接 100%），SSE 推送。
- **限流**：Semaphore 控制并发；云端 API 注意每分钟配额，网关层做令牌桶。

---

## 10. 落地路线

| 阶段 | 内容 |
|------|------|
| V1 | 单机：media_tasks 表 + asyncio 队列 + 两个插件 + 本地模型子进程（vidgen/ + 复用 ttsclone/）。完全复用现有模式，最快落地 |
| V2 | 模型网关接云端 API（Kling/Runway/Sora、火山/阿里 TTS），支持按 成本/质量/速度 路由；提示词增强与分镜用 LLM 自动生成 |
| V3 | 分布式队列（Celery/Redis）+ GPU worker 横向扩展；产物迁对象存储（S3/OSS）+ CDN；任务断点续跑 |

---

## 附：API 示例

```http
POST /api/media/tasks
Content-Type: application/json

{
  "type": "image_to_video",
  "asset_id": "doc_uploads 返回的 doc_id 或直接传 asset_path",
  "prompt": "云层缓慢流动，镜头从远山缓缓推近，黄昏光线",
  "params": { "duration": 5, "fps": 24, "model": "local" }
}
→ 202 { "task_id": "…", "status": "queued" }

GET /api/media/tasks/{task_id}
→ { "task_id": "…", "status": "running", "stage": "generate", "progress": 0.65 }

GET /api/media/tasks/{task_id}/events        # SSE
→ data: {"stage": "generate", "progress": 0.65, "log": "clip 2/3 done"}

POST /api/media/tasks/{task_id}/cancel       # 取消
GET /api/generated/download/xxx.mp4          # 下载产物（已存在）
```
