# Knowledge Base System — RAG + AI Agent

基于 RAG（检索增强生成）的知识库 AI 问答系统，支持文档上传、向量检索、多模型对话、Skills 和 Plugins 动态扩展。

---

## 功能

| 功能 | 说明 |
|------|------|
| **智能问答** | 上传文档后，通过 RAG 检索相关内容，结合 LLM 生成精准回答 |
| **多 Agent 编排** | Supervisor Agent 自动分析用户意图，路由到最合适的子 Agent 并行处理，支持多 Agent 流式响应 |
| **多模型支持** | 前端下拉菜单切换 DeepSeek V3 / R1、OpenAI GPT-4o / 4o-mini |
| **文档管理** | 支持 TXT / MD / PDF 上传，自动**章节感知分块**、向量化存储到 ChromaDB |
| **混合检索** | 向量检索 + BM25 关键词检索融合（RRF 排序），精确匹配与语义搜索兼顾 |
| **结构化章节检索** | 用户问题命中章节关键词（如"第一章"）时，**跳过向量检索**，直接查章节元数据表返回精确结果 |
| **父子文档结构** | 分块时自动生成父文档（章节标题+摘要）和子文档（正文块），检索子文档时携带父文档标题注入 LLM 上下文 |
| **中文支持** | 内置 `BAAI/bge-small-zh-v1.5` 中文嵌入模型，准确理解中文语义 |
| **上传进度** | 上传过程实时显示进度条（0-100%）及阶段描述（上传→分块→嵌入→入库） |
| **多轮对话** | 自动生成 conversation_id，支持同一会话内的上下文连续对话 |
| **会话隔离** | 每个会话独立消息存储，切换会话时消息互不干扰，后台流式请求继续运行 |
| **会话持久化** | IndexedDB 本地缓存 + 服务器 SQLite 双重持久化，页面刷新/SSE 中断不丢失消息（含 DataCloneError 修复：JSON 序列化剥离不可克隆值） |
| **会话管理系统** | 归一化会话库：用户/项目/工作区三级隔离、子会话树与 fork、上下文纪元、压缩基线持久化、消息撤销（revert）、AgentBus 任务自动登记为子会话 |
| **错误重试机制** | 三层重试架构：litellm 内置重试 → AgentBus 事件循环自愈 → 前端自动重试倒计时 + 手动重试按钮 |
| **并发控制** | 后端全局 Semaphore 限制同时运行的 Agent 任务数（`MAX_CONCURRENT_AGENTS` 默认 4），超出自动排队，前端实时显示排队/流式状态；session.db 使用 SQLite 连接池（WAL + busy_timeout），避免高并发连接反复开关 |
| **Skills（技能）** | Markdown 文件定义技能，动态加载，可在 Web 界面启用/禁用 |
| **Plugins（插件）** | Python 文件定义 tool_* 函数（如搜索、天气、生成文档），Agent 按需调用 |
| **HTTP 客户端** | Agent 可直接发起 HTTP 请求测试 API 接口（GET/POST/PUT/DELETE），支持自定义 headers 和 body |
| **Vector DB 开关** | 用户可在聊天界面手动控制是否启用向量库检索，关闭后 Agent 仅凭自身知识回答 |
| **生成文件管理** | Agent 创建的文档（.docx/.pdf/.xlsx/.pptx）可在独立页面查看、搜索、下载和删除，PDF 支持中文显示 |
| **本地 Embedding** | 使用 sentence-transformers 本地运行，通过 ModelScope 下载模型 |
| **检索重排序** | Cross-encoder 对检索结果重打分（top-3），显著提升回答精度 |
| **上下文管理** | tiktoken 精确 token 计数 + 上下文预算控制 + 工具输出智能边界/回溯清理 + 工具结果去重，防止 context 膨胀 |
| **对话持久化** | SQLite 存储对话历史，服务重启不丢失 |
| **来源引用** | 回答时标注检索到的文档来源及相似度分数 |
| **系统监控** | 请求级日志（方法/路径/状态/耗时）+ LLM 调用统计（模型/token/耗时/工具轮数），Web 页面可视化展示 |
| **消息滚动** | 聊天消息列表智能自动滚动（靠近底部才跟随），用户上翻浏览时不强制滚动 |
| **权限系统** | AI Agent 写外部路径时前端弹窗审批，支持白名单持久化；可写工作目录由前端「工作目录」面板配置（运行时生效、持久化、免重启），`EXTERNAL_PATH_DEFAULT` 控制外部路径默认策略、审批超时（`PERMISSION_APPROVAL_TIMEOUT`）可配置，总线路径无可解释拒绝而非静默失败；**Shell 重定向目标权限检查**：`>`、`>>`、`2>`、`2>>`、`&>` 写入的外部文件经过权限检查 |
| **任务执行引擎** | 参考 OpenCode 双层循环架构，任务持续执行直到完成，支持上下文压缩和最大步数限制 |
| **执行循环护栏** | `MAX_STEPS`（默认 40）最后轮注入收尾提示并禁用工具，强制"已完成/未完成/下一步"结构化总结；Doom-loop 检测（连续相同工具调用 ≥3 轮）注入策略变更提示，升级到上限后强制收尾 |
| **任务完成判断** | 读取 `finish_reason` 归一化为 OpenCode 六值语义，仅 `tool-calls` 维持工具循环，`length` 追加截断提示、`content-filter` 转为可解释错误 |
| **长任务不截断** | 输出上限 `LLM_MAX_TOKENS`（默认 16384）可配置；系统提示强制长内容（>500 字）写入文件、回复只留摘要，避免单轮输出触发截断（对齐 OpenCode 长任务机制） |
| **子代理超时分级** | 工具密集型子 Agent（如 code）使用更长等待（`SUB_AGENT_TIMEOUT_EXTENDED` 默认 300s）；子 Agent 仍活跃时自动宽限续期；失败时结构化回传已完成步骤 + 失败原因 + 建议 |
| **上下文压缩** | 消息超阈值（自动 = 可用预算的 60%，默认 ≈ 91K）时将旧消息压缩为锚定式结构化 checkpoint，保留最近 N 轮工具上下文 |
| **共享记忆持久化** | Agent 记忆（短期事实/偏好）持久化到 `data/agent_memory.json`，set/delete/clear 即时落盘（可异步去抖），重启不丢失；主 Agent 可通过 `tool_memory_set/get/search` 主动记忆/回忆/检索 |
| **SSRF 防护** | 所有出站 HTTP（`http_client` 插件、`tool_execute` 中的 curl/wget/ssh 等命令）经 `check_url`/`_ssrf_check_command` 校验，拦截内网/回环/链路本地/元数据地址及解析到它们的域名；`SSRF_ALLOW_INTERNAL=true` 可跳过（本地调试） |
| **用户身份签名** | 可选 `AUTH_TOKEN_SECRET`：trust-on-first-use 注册 → 服务端仅存 HMAC 哈希 → 颁发签名 token（`base64(uid).base64(exp).hmac`）；中间件强制每个 `/api/*` 请求携带 `X-User-Id` + `X-Auth-Token`，未配置时全部关闭（默认本地行为） |
| **多 Agent 工具链** | `code`/`web_search` 子 Agent 使用 LLM 工具调用循环（≤8 轮）+ 工作区文件系统工具（读写/编辑/搜索/执行），复用统一权限系统；外部路径经权限桥发事件并拒绝而非静默失败 |
| **文件附件** | 聊天输入框拖拽/粘贴/选择上传附件（图片/PDF/Word/Excel/TXT/MD 等），图片以多模态 `image_url` 直送 LLM，文档经 **LangChain 文档加载器**（`attachment_loader.py`：pypdf/python-docx/openpyxl）解析为文本上下文；附件随消息持久化（服务端 `data.files`），刷新/回放仍可回显 |
| **移动端响应式** | 统一手机端适配模块 `frontend/src/styles/mobile.css`（`main.ts` 引入，Vite 打包）：768/480px 断点，覆盖全部视图与组件 —— 侧栏抽屉化、触控友好按钮/输入、iOS 输入字号与 safe-area、聊天工具条换行、统计二列、弹窗底部 sheet/全屏、表格横滚等 |

---

## 文档上传进度机制

文档上传采用**异步任务 + 轮询**模式，前端实时显示量化进度：

| 阶段 | 进度 | 说明 |
|------|------|------|
| Uploading to server | 0-4% | 文件上传到服务器（XHR onprogress） |
| Queued for processing | 5% | 进入后台处理队列 |
| Saving file | 5% | 保存文件到磁盘 |
| Reading and chunking | 15% | 读取文件内容，章节感知分块（按 `第X章`/`Chapter X` 边界） |
| Generating embeddings | 25% | 开始生成嵌入向量 |
| Embedding (x/total chunks) | 25-90% | 逐批生成向量（最耗时的阶段，按 chunk 数精确量化） |
| Storing to vector database | 92% | 存入 ChromaDB |
| Complete | 100% | 处理完成，文档可用 |

**工作流程：**

1. `POST /api/documents/upload` 立即返回 `{ task_id }`
2. 后端 `TaskManager` 在后台依次执行：保存→分块→嵌入→入库
3. 前端每 400ms 轮询 `GET /api/documents/tasks/{task_id}` 获取 `{ progress, stage }`
4. 进度 100% 后，前端将文档加入列表

实现路径：
- 后端：`backend/app/services/task_manager.py` — `TaskManager.process_document()`
- 前端：`frontend/src/api/documents.ts` — `uploadDocument()` → `pollTask()`

---

## 中文支持与混合检索

### 中文嵌入模型

默认嵌入模型为 `BAAI/bge-small-zh-v1.5`，专门针对中文优化。在 `.env` 中可切换：

```ini
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5    # 中文（推荐）
# EMBEDDING_MODEL=all-MiniLM-L6-v2        # 英文
```

模型通过 ModelScope 自动下载。

### 分块机制

上传文档后，`document_processor.py` 执行四步分块：

**① 章节检测** — 正则匹配 `第X章` / `Chapter X` / `CHAPTER X` 自动识别章节边界，无标记则整篇作为一章。

**② 父子文档结构** — 每章生成两类文档块：

| 类型 | 内容 | 用途 |
|------|------|------|
| **父文档** | 章节标题 + 前 300 字摘要（`is_parent: true`） | 章节概览检索，精确回答"第一章讲了什么" |
| **子文档** | 正文按 500 字滑动窗口切分（`is_parent: false`） | 细节检索，覆盖全文 |

**③ 滑动窗口分块** (`_chunk_text`)：

```
CHUNK_SIZE=500     # 每块最大字数
CHUNK_OVERLAP=200  # 块间重叠 200 字，避免切句时断裂
```

**④ 角色-台词锚点块** — 每章扫描对话模式（`张三说："..."` / `"..."张三说`），为每句台词生成独立锚点：

```
text: 张三说：“你好吗？”
metadata: {is_dialogue: true, speaker: "张三", dialogue: "你好吗？"}
```

台词锚点块角色名+台词语义高度集中，搜索"张三说了什么"可直接命中。

子文档保留 `chapter_title`/`chapter_number` 元数据，检索后强制注入父文档标题到 LLM 上下文。

实现路径：`backend/app/rag/document_processor.py` — `process()` → `_split_chapters()` → `_chunk_text()` → `_extract_dialogues()`

### 混合检索（Hybrid Search）

结合两种检索方式：

| 方式 | 优势 | 适用场景 |
|------|------|----------|
| **向量检索** | 语义理解，找"意思相近" | 开放式问题、概括性查询 |
| **BM25 关键词** | 精确匹配，找"字面相同" | 章节名、人名、术语等事实查询 |

两者通过 **RRF（Reciprocal Rank Fusion）** 算法融合，权重向量 0.7 + BM25 0.3。

**多路台词召回** — 检索时额外执行一路台词锚点搜索（`where={"is_dialogue": true}`），与主路径结果经 RRF 融合（台词权重 0.4），确保角色台词不被正文淹没。

实现路径：
- BM25 索引：`backend/app/rag/bm25_index.py` — `BM25Index`
- 混合检索：`backend/app/rag/retriever.py` — `Retriever.invoke()`（含 RRF 融合 + 多路台词召回）

### 结构化章节检索（两步走）

解决"RAG 不能说出准确章节名"的工业级方案：

```
用户问 "第一章讲了什么"
  │
  ├─ intent.py: 正则匹配 → {chapter_number: 1}
  │     ↓
  ├─ 前置过滤：直接查 ChapterStore（SQLite 章节元数据表）
  │     ├─ 返回精确的 chapter_title + summary
  │     └─ 不再走向量检索
  │     ↓
  └─ LLM context 中携带 chapter_title + content
       → 准确回答，不再"猜章节名"
```

**两步走策略**：

| 步骤 | 触发条件 | 行为 |
|------|----------|------|
| **1. 前置过滤** | 问题含 `第X章`/`Chapter X`/`关于...章节` 等关键词 | 关闭向量检索，精确查章节元数据表 |
| **2. 混合检索** | 无章节关键词 | 正常向量+BM25 混合检索，但结果自动携带父文档章节标题 |

**父子文档结构**：

上传文档时，每个章节生成两种文档块：
- **父文档**：章节标题 + 前 300 字摘要 → 用于章节概览
- **子文档**：正文 1000 字分块 → 用于细节检索

子文档 metadata 中保留 `chapter_title`，检索后强制注入父文档章节标题到 LLM Prompt。

实现路径：
- 章节元数据表：`backend/app/rag/chapter_store.py` — `ChapterStore`
- 意图识别：`backend/app/rag/intent.py` — `detect_chapter_intent()`
- 父子分块：`backend/app/rag/document_processor.py` — `process()` 返回 `(chunks, chapter_metas)`
- 两步检索：`backend/app/rag/retriever.py` — `Retriever.invoke()` → `_chapter_lookup()` / `_enrich_with_parent()`

---

### Vector DB 开关控制

用户通过前端聊天界面的 **Vector DB** 开关手动控制是否启用知识库检索，系统不自动判断：

```
前端 Toggle（useVectorDb）
       │
       ▼
ChatRequest.use_vector_db: bool
       │
       ▼
AgentState.use_vector_db
       │
       ▼
_retrieve(state)
   │
   ├─ use_vector_db = false
   │     └─ return {context: [], sources: []}    ← 跳过向量库
   │
   └─ use_vector_db = true
         ├─ 向量检索 + BM25 混合检索
         └─ return {context, sources}             ← 注入知识库内容
              │
              ▼
         _generate(state)
              ├─ context 不为空 → system prompt 拼接知识库内容 → LLM 基于知识库回答
              └─ context 为空   → 纯 LLM system prompt（不携带知识库上下文）
```

**关键点**：LLM **始终被调用**，区别只在于 prompt 中是否包含检索到的知识库内容。开关关闭后 Agent 仅凭模型自身知识回答（联网搜索等插件工具仍可用）。

实现路径：`backend/app/agent/graph.py:64-78` — `_retrieve()` 检查 `state.get("use_vector_db", True)`

---

## 技术栈

| 层 | 技术 |
|---|---|
| **后端框架** | Python 3.14+, FastAPI, Uvicorn |
| **AI Agent** | LangGraph（retrieve → rerank → generate 工作流）, LangChain |
| **LLM 调用** | LiteLLM（统一 DeepSeek / OpenAI / Ollama API） |
| **向量数据库** | ChromaDB（本地持久化，余弦相似度） |
| **文本嵌入** | sentence-transformers（all-MiniLM-L6-v2，通过 ModelScope 下载） |
| **检索重排序** | Cross-encoder（cross-encoder/ms-marco-MiniLM-L-6-v2，通过 ModelScope 下载） |
| **对话存储** | SQLite（本地持久化对话历史） |
| **前端框架** | Vue 3, TypeScript 5.7, Vite 6 |
| **状态管理** | Pinia |
| **路由** | Vue Router 4 |
| **文档解析** | LangChain 文档加载器：pypdf（PDF）、python-docx（Word）、openpyxl（Excel）、TextLoader（TXT/MD/JSON/CSV）——聊天附件与知识库上传共用 |
| **关键词检索** | BM25（rank_bm25）+ jieba 中文分词 |
| **文档生成** | python-docx（Word）、reportlab（PDF，微软雅黑字体）、openpyxl（Excel）三大生成引擎 |
| **互联网搜索** | Tavily API 实时搜索新闻、网页、财经信息 |
| **天气查询** | Open-Meteo API（免费，无需 key）获取天气实况和预报 |
| **生成文件管理** | Web 页面浏览/搜索/下载/删除 Agent 生成的 .docx/.pdf/.xlsx 文件 |
| **文本分块** | 父子文档分块（parent=章节摘要，child=正文块），按 `第X章`/`Chapter X` 边界 |
| **章节元数据** | SQLite 章节映射表（`ChapterStore`），支持章节号/标题精确查询 |
| **查询意图识别** | 正则匹配 `第X章`/`Chapter X`/`关于...章节`，自动选择检索策略 |

---

## 环境安装

### 前置要求

- Python >= 3.14
- Node.js >= 18
- LLM API Key（DeepSeek / OpenAI）

### 后端

```bash
cd backend

# （可选）创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
# source .venv/bin/activate

# 安装依赖（推荐 uv，速度更快）
pip install uv
uv sync
# 或直接用 pip
# pip install -r requirements.txt

# 配置环境变量（首次需要）
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 模型（首次启动通过 ModelScope 自动下载 Embedding 和 Reranker 模型）
```

### 前端

```bash
cd frontend
npm install
```

---

## 启动与停止

### 启动后端

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- 启动后访问 `http://localhost:8000` 查看服务信息
- API 文档：`http://localhost:8000/docs`（Swagger UI）
- 健康检查：`http://localhost:8000/health`

### 停止后端

按 `Ctrl + C` 终止 uvicorn 进程即可。

### 启动前端

```bash
cd frontend
npm run dev
```

- 前端默认运行在 `http://localhost:5173`
- Vite 自动代理 `/api` 请求到后端 `localhost:8000`

### 停止前端

按 `Ctrl + C` 终止 Vite 进程即可。

### 生产构建

```bash
cd frontend
npm run build          # 构建到 frontend/dist/
npm run preview        # 本地预览构建产物
```

---

## 配置说明

编辑 `backend/.env`：

```ini
# ===== LLM =====
LLM_MODEL=deepseek/deepseek-v4-flash
LLM_API_KEY=sk-xxxxxxxxxxxx
LLM_API_BASE=https://api.deepseek.com

# ===== Embedding =====
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5     # 中文模型；英文文档可用 all-MiniLM-L6-v2  也需要 重排序模型  ms-marco-MiniLM-L-6-v2

# ===== RAG =====
VECTOR_STORE_PATH=data/vector_store
UPLOAD_DIR=data/uploads
CHUNK_SIZE=1000
CHUNK_OVERLAP=200

# ===== Reranker =====
ENABLE_RERANKER=true
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

# ===== Skills & Plugins =====
SKILLS_DIR=skills
PLUGINS_DIR=plugins

# ===== Tavily（互联网搜索）======
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxx

# ===== LLM 输出上限 =====
# 每次 LLM 调用的输出 token 上限（默认 16384）；长任务配合系统提示"长内容写文件"规则避免截断
# LLM_MAX_TOKENS=16384
```

### 上下文预算与压缩

所有配置项均可在 `backend/.env` 中调整（默认值见注释，全部默认开启无需手动配置）：

```ini
# ===== Context Budget & Compaction =====
# 单次 LLM 调用的上下文上限（默认 160000）
# MAX_CONTEXT_TOKENS=160000
# 输出预留 token（默认 8192）——usable = MAX_CONTEXT_TOKENS - CONTEXT_RESERVE_TOKENS
# CONTEXT_RESERVE_TOKENS=8192
# 压缩触发阈值；0 = 自动（0.6 × usable ≈ 91084）
# COMPACTION_THRESHOLD_TOKENS=0
# 压缩保留的最近轮次（默认 2）
# CONTEXT_TAIL_TURNS=2
# 尾部保留 token 预算（默认 8000）
# CONTEXT_PRESERVE_RECENT_TOKENS=8000
# 工具输出回溯清理：保护下限 / 生效下限（默认 40000 / 20000）
# TOOL_OUTPUT_PROTECT_TOKENS=40000
# TOOL_OUTPUT_PRUNE_MINIMUM_TOKENS=20000
```

### 安全与系统

```ini
# ===== Security =====
# 可选：启用签名身份校验（默认关闭）。设置后中间件强制每个 /api/* 请求携带 X-User-Id + X-Auth-Token
# AUTH_TOKEN_SECRET=change-me-to-a-long-random-secret
# 认证 token 有效期（秒，默认 2592000 = 30 天）
# AUTH_TOKEN_TTL=2592000
# 注册用户存储路径
# AUTH_USERS_PATH=data/auth_users.json
# SSRF 防护：设为 true 时跳过内网/回环校验（仅本地调试用，勿在生产开启）
# SSRF_ALLOW_INTERNAL=true
# 并发 Agent 上限（默认 4），与 session 协调器共享
# MAX_CONCURRENT_AGENTS=4
# Agent 记忆持久化路径
# MEMORY_PERSIST_PATH=data/agent_memory.json
# 工具审批超时（秒，默认 60）
# PERMISSION_APPROVAL_TIMEOUT=60
# 外部路径默认策略：ask / allow / deny
# EXTERNAL_PATH_DEFAULT=ask
```

### 切换 LLM 提供商

| 提供商 | LLM_API_BASE | LLM_MODEL 示例 |
|--------|-------------|----------------|
| DeepSeek | `https://api.deepseek.com` | `deepseek/deepseek-v4-flash` / `deepseek/deepseek-v4-pro` |
| OpenAI | 留空 | `gpt-4o` / `gpt-4o-mini` |
| Ollama（本地） | `http://localhost:11434` | `ollama/qwen2.5:7b` |

---

## 用户身份认证

默认（`AUTH_TOKEN_SECRET` 未设置）下，所有 API 请求通过 `X-User-Id` 请求头标识用户身份，未携带时后端默认使用 `"anonymous"`。

```javascript
fetch("http://localhost:8000/api/chat/multi-agent", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-User-Id": "user_xxx",  // ← 用户身份
  },
  body: JSON.stringify({ message: "你好" }),
})
```

### 可选：签名身份校验（`AUTH_TOKEN_SECRET`）

在 `.env` 设置 `AUTH_TOKEN_SECRET` 后启用可信身份签名，防止伪造用户 ID：

1. **Trust-on-first-use 注册**：前端生成随机 `user_id` + `device_secret`，`POST /api/auth/register` 上报，服务端仅存 HMAC 哈希（不存明文密钥）
2. **签发 token**：`POST /api/auth/token` 返回签名 token（`base64(uid).base64(exp).hmac(uid:exp:reg_hash)`），有效期 `auth_token_ttl`（默认 30 天）
3. **强制校验**：`AuthMiddleware` 要求每个 `/api/*` 请求携带 `X-User-Id` + `X-Auth-Token`（跳过 `/api/auth/*`、非 `/api` 路径、OPTIONS），校验签名与过期时间

| 文件 | 作用 |
|------|------|
| `backend/app/auth.py` | HMAC 哈希、token 签名/校验（`create_signed_token` / `verify_signed_token`）+ `AuthMiddleware`（未配置密钥时跳过） |
| `backend/app/api/auth.py` | `POST /api/auth/register` + `POST /api/auth/token` 路由（账户登录见 `backend/app/api/auth.py` 的 `/api/auth/account/*`，PBKDF2 哈希 + JWT） |
| `frontend/src/api/auth.ts` | `ensureAuth()` — 生成/读取 `user_id` + `device_secret`，自动注册并换取 token（`main.ts` 启动时调用） |
| `frontend/src/api/fetch.ts` | `addAuthHeaders()` 自动注入 `X-User-Id` + `X-Auth-Token`；`fetchWithTimeout` 封装自动带认证头 |

后端通过 `_get_user_id(request)` 统一提取；启用签名校验后该函数同时验证 token，前端接口不变。

### 会话类型隔离

聊天对话按类型隔离存储，不同前端会话列表互不干扰：

| 类型 | 前端页面 | kind 值 |
|------|----------|-------------|
| **多 Agent 编排** | MultiAgentView | `multi-agent`（默认） |
| **任务子会话** | 内部使用 | `task` |

> 单 Agent 聊天（`chat` 类型）及其前端入口（ChatView / MobileView）已随单 Agent 模式移除，`/chat`、`/m` 路由删除，`/` 重定向到 `/multi-agent`。

前端通过 `GET /api/sessions?kind=multi-agent` 过滤多 Agent 会话列表。

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查 |
| POST | `/api/chat/multi-agent/` | 发送聊天消息（多 Agent Supervisor） |
| POST | `/api/chat/multi-agent/stream` | 流式聊天 SSE（多 Agent），支持 queued/routing/agent_start/agent_stream/agent_done/agent_error/permission_request/done/error 事件 |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions` | 会话列表（`project`/`roots`/`search`/`archived`/`kind`） |
| GET / PATCH / DELETE | `/api/sessions/{id}` | 详情 / 更新 / 删除（级联子会话） |
| POST | `/api/sessions/{id}/fork` | 在指定 `message_id` 处 fork 子会话 |
| GET | `/api/sessions/{id}/messages?after_seq=` | 分页消息（每条附 `parts`） |
| DELETE | `/api/sessions/{id}/messages/{message_id}` | 删除单条消息 |
| GET | `/api/sessions/{id}/context` | 模型视角上下文（epoch + 过滤后历史） |
| POST | `/api/sessions/{id}/compact` | 手动压缩（可选 `checkpoint`） |
| POST | `/api/sessions/{id}/revert` | 撤销到指定 `message_id` |
| POST | `/api/sessions/{id}/interrupt` | 打断（级联子会话 + 取消任务） |
| GET | `/api/sessions/{id}/children` | 子会话列表 |
| GET | `/api/sessions/{id}/status` | 状态 + 队列深度 |
| POST | `/api/documents/upload` | 上传文档（multipart），返回 task_id 异步处理 |
| GET | `/api/documents/tasks/{task_id}` | 查询上传任务进度（progress + stage） |
| GET | `/api/documents/` | 文档列表 |
| DELETE | `/api/documents/{id}` | 删除文档 |
| GET | `/api/skills/` | 技能列表 |
| POST | `/api/skills/{name}/toggle` | 启用/禁用技能 |
| GET | `/api/plugins/` | 插件列表 |
| POST | `/api/plugins/{name}/toggle` | 启用/禁用插件 |
| GET | `/api/vectors/?offset=0&limit=50&query=xxx&document_id=xxx` | 查看/搜索向量库内容（分页+全文检索） |
| GET | `/api/generated/` | 列出 Agent 生成的文档（可选 `?q=关键字` 搜索文件名） |
| GET | `/api/generated/download/{filename}` | 下载生成的文档（.docx/.pdf/.xlsx 等） |
| DELETE | `/api/generated/{filename}` | 删除生成的文档 |
| GET | `/api/monitor/stats` | 系统监控统计（请求量/模型调用/token 用量/耗时） |
| POST | `/api/auth/register` | 注册设备身份（`user_id` + `device_secret`，仅存 HMAC 哈希） |
| POST | `/api/auth/token` | 签发签名 token（需 `AUTH_TOKEN_SECRET`） |
| GET | `/api/auth/status` | 查询是否启用签名校验（前端据此决定是否自动注册） |
| GET | `/api/config/summarization` | 查看摘要模型配置 |
| POST | `/api/config/summarization` | 运行时切换摘要模型 |

---

## 聊天界面控制

| 控制项 | 说明 |
|--------|------|
| **Vector DB 开关** | 控制是否启用向量库检索。开启后 Agent 会检索上传的文档内容辅助回答；关闭后仅凭 LLM 自身知识回答，适合闲聊或通用问题。 |
| **模型选择** | 下拉切换 DeepSeek / OpenAI 等 LLM 模型。 |
| **流式状态** | Header 实时显示当前会话状态：排队中（⏳ #N）或流式传输中（● streaming）。Sidebar 每个会话旁也有状态标签。 |

---

## 智能体上下文管理

每次对话请求通过 `conversation_id` 进行会话关联：

1. **自动生成 ID**：首次对话不传 `conversation_id`，后端创建 `kind='multi-agent'` 会话并返回
2. **历史加载**：`_session_history_for()` 传 `conversation_id` 则从 SQLite 恢复完整历史（正文优先取 `message_parts` 的 text part）
3. **滑动窗口截断**：`_truncate_history()` 限制 16000 tokens，超出的最早消息被丢弃，插入 `[earlier history truncated]` 占位
4. **摘要压缩（可选，分层）**：配置 `SUMMARIZATION_MODEL` 后在截断前先尝试用 LLM 压缩早期消息。旧消息按 `CHUNK_PAIRS`（默认 10 对）分批，每批独立压缩为子摘要，若子摘要合并后仍超阈值则递归合并，直到适应预算。保留最近的 `SUMMARIZATION_KEEP_MESSAGES`（默认 20）条完整消息。摘要失败时自动回退为截断。
5. **持久化**：每次回答后，user/assistant 消息对追加写入 `data/session.db`（主会话 + `kind='task'` 子会话各一份，含 sources/steps/agents/tokens）

> 截断在 LLM 调用前发生，仅影响 prompt 传入的历史，不影响数据库中的完整记录。

实现路径：`backend/app/api/chat.py` — `_session_history_for` / `_truncate_history` / `_persist_multi_agent`，`backend/app/middleware/summarization.py` — `HierarchicalSummarizationMiddleware`

### 共享记忆持久化（Memory）

`MemoryManager`（`backend/app/agent/memory.py`）提供跨会话的短期记忆（Agent 主动记录的临时事实/偏好），未过期条目在 set/delete/clear 时写入 `settings.memory_persist_path`（默认 `data/agent_memory.json`），启动时重载，服务重启不丢失。`persist_path` 为构造函数注入，便于测试隔离。

**主 Agent 记忆工具**：主 RAG Agent（`graph.py`）注入共享 `MemoryManager`（`runtime.py`），模型可自主调用 `tool_memory_set(key, value, tags?)` / `tool_memory_get(key)` / `tool_memory_search(tag)` 记忆、回忆、按标签检索关键信息（对齐 opencode memory 语义）。记忆按 `conversation_id` 命名空间隔离，跨会话不可见；未注入记忆管理器时不注册工具。子 Agent（`code`/`web_search`）复用同一实例，主 Agent 记住的信息子 Agent 也可检索到。

**持久化优化**：`_persist_sync()` 保持"set 返回即落盘"的同步语义；另提供 `_persist_async_debounced()`（异步 `asyncio.to_thread` + 1 秒去抖窗口），用于高并发多 Agent 写共享记忆时避免阻塞各自事件循环（对齐 opencode 异步 storage 语义）。

---

## 会话管理系统（Session Management）

基于 SQLite（`backend/data/session.db`）的归一化会话体系，对齐 OpenCode 的 session 模型，为多 Agent 编排与任务执行提供统一底座。旧 `conversations.db` 保持只读，首次访问时惰性迁移（`conversation_id == session.id`），前端无需改动。

### 核心概念

| 概念 | 说明 |
|------|------|
| **会话（Session）** | 用户/项目/工作区三级隔离；`parent_id` 构成子会话树（fork / 任务共享） |
| **消息日志** | `session_messages` append-only 事件日志，会话内自增 `seq` 水位 |
| **上下文纪元（Context Epoch）** | 每个会话持久化系统上下文快照 + `baseline_seq`，恢复/压缩后精确定位历史水位 |
| **压缩基线（Compaction Baseline）** | 压缩发生时落 `compaction` 消息 + 重建 epoch；checkpoint 作为 system 上下文带回，重启不丢摘要 |
| **撤销（Revert）** | 删除指定消息之后的所有消息与部件，并回滚纪元水位 |

### 特性

- **per-session 串行 + 全局并发上限**：`service.write_lock(session_id)` 保证同一会话串行；`_agent_semaphore`（`app/api/chat.py`）受 `MAX_CONCURRENT_AGENTS` 全局限流（超出自动排队，SSE 发 `queued` 事件）
- **fork**：在任意消息处克隆子会话，独立消息日志与上下文，与父会话互不污染
- **级联删除 / 取消**：删除或打断会话时级联其子会话，并取消对应的 AgentBus 后台任务（`task_bridge.cancel_children`）
- **多 Agent 任务**：`/api/chat/multi-agent` 与 `/stream` 把每次任务登记为 `kind='task'` 子会话，父/子消息同步回写
- **压缩基线持久化**：历史超阈值被摘要/截断时落 `compaction` 消息 + 置 `time_compacted`；`history.load` 始终带回最新 checkpoint

### REST API（前缀 `/api/sessions`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions` | 列表（`project`/`roots`/`search`/`archived`/`kind`） |
| GET / PATCH / DELETE | `/api/sessions/{id}` | 详情 / 更新 / 删除（级联） |
| POST | `/api/sessions/{id}/fork` | 在指定 `message_id` 处 fork 子会话 |
| GET | `/api/sessions/{id}/messages` | 分页消息（`after_seq`，每条附 `parts`） |
| DELETE | `/api/sessions/{id}/messages/{message_id}` | 删除单条消息 |
| GET | `/api/sessions/{id}/context` | 模型视角上下文（epoch + 过滤后历史） |
| POST | `/api/sessions/{id}/compact` | 手动压缩（可选 `checkpoint`） |
| POST | `/api/sessions/{id}/revert` | 撤销到指定 `message_id` |
| POST | `/api/sessions/{id}/interrupt` | 打断（级联子会话） |
| GET | `/api/sessions/{id}/children` | 子会话列表 |
| GET | `/api/sessions/{id}/status` | 状态 + 队列深度 |

> 所有 `/api/sessions` 请求经 `X-User-Id` 头隔离（默认 `anonymous`），跨用户访问返回 403。

实现路径：`backend/app/session/`（db / models / repository / history / service / deps / router / agent_executor / task_bridge；`coordinator.py` 已随单 Agent 移除）；设计文档见 `docs/session-management-design.md`。

---

## 会话隔离（Session Isolation）

前端采用**按会话隔离**的消息存储架构，确保切换历史会话时消息互不干扰：

### 数据结构

```typescript
// 每个会话独立存储
const sessions = ref<Record<string, SessionState>>({
  'session-id-1': {
    messages: [...],
    conversationId: 'conv-123',
    conversationTitle: '第一章讲了什么...',
    currentSteps: [],
    loading: false,
    abortController: null,
    streamPhase: 'running',   // 'idle' | 'queued' | 'running'
    queuePosition: null,       // 排队位置，null 表示不在排队
  },
  'session-id-2': { ... }
})

// 当前活跃会话
const activeSessionId = ref<string | undefined>(undefined)
```

### 核心优势

| 特性 | 说明 |
|------|------|
| **消息隔离** | 切换会话时，每个会话显示自己的消息列表 |
| **后台请求** | 流式请求在后台继续运行，完成时消息保存到对应会话 |
| **状态独立** | 每个会话有自己的 loading、currentSteps、streamPhase 状态 |
| **即时切换** | 已加载的会话切换时无需重新请求服务器，本地消息完整保留 |
| **内容不丢失** | 切换会话时，正在 streaming 的消息保留在本地，切回时立即可见 |

### 双重持久化（IndexedDB + 服务器）

解决 SSE 中断/页面刷新时消息丢失的问题：

```
发送消息
  │
  ├─ 1. 立即写入 IndexedDB（防 SSE 中断丢失 user 消息）
  ├─ 2. SSE 流式接收 → 消息在前端内存中累积
  ├─ 3. done 事件 → 写入 assistant 消息
  │     ├─ 写入 IndexedDB（本地缓存）
  │     └─ 后端写入 SQLite（持久化，含 sources/steps）
  │
  ▼
切换会话 → loadConversation()
  │
  ├─ 始终从服务器获取最新数据（不再跳过）
  ├─ 从 IndexedDB 加载本地缓存
  └─ 合并策略：
       ├─ 服务器有、缓存没有 → 用服务器数据
       ├─ 缓存有、服务器没有 → 补入（SSE 中断时的 user 消息）
       └─ 两端都有 → 取 content 更长或有 sources/steps 的版本
```

**解决的场景：**

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| SSE 中途断开 | user 消息丢失，assistant 为空 | IndexedDB 保留 user 消息，下次加载时从服务器合并 |
| 页面刷新 | 内存清空，服务器 assistant 为空 | 从 IndexedDB 恢复 + 服务器合并 |
| 切换会话再切回 | 有时消息丢失 | 始终从服务器同步，合并本地缓存 |

实现路径：
- 缓存层：`frontend/src/api/session-cache.ts` — IndexedDB CRUD + 合并逻辑
- Store 集成：`frontend/src/stores/multiAgent.ts` — `loadConversation()` / `persistSession()`
- 后端持久化：`backend/app/api/chat.py` — `_persist_multi_agent()` 在 `done`/`error` 事件时保存 `sources`/`steps`/`agents`/`tokens` 到 SQLite（主会话 + task 子会话）

### 工作流程

```
用户在会话A发送消息
    │
    ├─ 创建 AbortController (key: session-A)
    ├─ streamPhase = 'queued' → 等待并发 slot
    ├─ 收到执行事件 → streamPhase = 'running'
    ├─ 开始流式接收
    │
    ▼
用户切换到会话B
    │
    ├─ activeSessionId = B
    ├─ 会话A的本地消息保留（不从服务器覆盖）
    ├─ 显示会话B的消息
    └─ 会话A的流式请求继续在后台运行
         │
         ▼
    会话A请求完成
         │
         ├─ streamPhase = 'idle'
         ├─ 消息保存到 sessions['session-A'].messages
         └─ 如果用户切回会话A，消息仍在（无需重新加载）
```

实现路径：`frontend/src/stores/multiAgent.ts` — `useMultiAgentStore`（按会话ID存储消息，支持 agents 面板、routing 状态与目录绑定，`kind='multi-agent'`）
- 单 Agent 版本（`stores/chat.ts`）与移动端（`stores/mobileChat.ts`）已随单 Agent 模式移除

### 并发控制

后端使用全局信号量（`_agent_semaphore`，`settings.max_concurrent_agents` 默认 4）限制同时运行的 Agent 任务数，防止多会话同时 streaming 导致资源争抢。多 Agent 请求经 `/api/chat/multi-agent` 与 `/stream` 进入队列，同一会话内仍严格串行执行（`service.write_lock`）：

```
Session A 发消息 → 获取 slot #1 → 开始执行
Session B 发消息 → 获取 slot #2 → 开始执行
Session C 发消息 → slot 已满 → 排队等待
    │
    ├─ SSE 事件: {type: "queued", queue_position: 1}
    ├─ Sidebar 显示 "⏳ #1"
    ├─ MultiAgentView Header 显示 "排队中 #1"
    │
    ▼
Session A 完成 → slot #1 释放 → Session C 获得 slot
    │
    ├─ SSE 事件: {type: "routing"} → 进入 running 阶段
    ├─ Sidebar 显示 "● streaming"
    └─ 开始流式输出
```

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MAX_CONCURRENT_AGENTS` | 4 | 同时运行的最大 Agent 任务数 |

**session.db 连接池**：`app/session/db.py` 的 `_ConnectionPool` 维持固定连接（池大小 = `max(6, max_concurrent_agents + 2)`），`_get_db()` 借出、`close()` 归还；WAL 模式 + `busy_timeout=10000` 保证并发写安全。非默认路径的 DB 连接绕过池。

> 旧的状态 API `GET /api/chat/stream/status`（单 Agent 时期）已随单 Agent 模式移除；排队状态现在由 SSE `queued` 事件 + `queue_position` 承载，前端 `streamPhase` 跟踪。

实现路径：
- 后端：`backend/app/api/chat.py` — `_agent_semaphore` + `run_agent()` / `_run_multi_agent_stream()` 的 `async with sem`
- 连接池：`backend/app/session/db.py` — `_ConnectionPool._get_db()` / `close()`
- 前端状态：`frontend/src/stores/multiAgent.ts` — `SessionState.streamPhase` / `queuePosition`
- 前端显示：`frontend/src/components/MultiAgentChatHistory.vue`（Sidebar 排队/运行中 badge）、`frontend/src/views/MultiAgentView.vue`（Header 标签）

---

## 错误重试机制（Error Retry）

参考 OpenCode 的重试架构，实现三层重试机制，覆盖 LLM API 故障、网络中断、SSE 断连等场景：

### 三层重试架构

```
┌─────────────────────────────────────────────────────┐
│                    前端层                             │
│  SSE 断连检测 + 重试按钮 + 自动重试倒计时(5s×2次)     │
└──────────────────────┬──────────────────────────────┘
                       │ SSE error / 断连
┌──────────────────────▼──────────────────────────────┐
│                   API 层                             │
│  错误分类(retryable/non-retryable) + 用户提示        │
└──────────────────────┬──────────────────────────────┘
                       │ 429/500/503
┌──────────────────────▼──────────────────────────────┐
│                 后端层                                │
│  litellm num_retries=2 + AgentBus 事件循环自愈(5次)  │
└─────────────────────────────────────────────────────┘
```

### 错误分类

| 错误类型 | 分类依据 | 可重试 | 后端重试 | 前端自动重试 |
|----------|----------|--------|----------|-------------|
| 429 Rate Limit | `RateLimitError` / `429` | ✅ | litellm 2次 + AgentBus 5次 | 5s 倒计时，最多 2 次 |
| 500/502/503 | `InternalServerError` / `5xx` | ✅ | litellm 2次 + AgentBus 5次 | 5s 倒计时，最多 2 次 |
| 网络断开 | `Failed to fetch` / SSE 断连 | ✅ | — | 5s 倒计时，最多 2 次 |
| 超时 | `timeout` / `timed out` | ✅ | litellm 2次 | 5s 倒计时，最多 2 次 |
| Context Overflow | `context_length_exceeded` | ❌ | — | — |
| 401/403 | 认证失败 | ❌ | — | — |
| 用户取消 | `AbortError` | ❌ | — | — |

### 退避策略

```
retryAfter header → 使用 header 值（上限 60s）
无 header → litellm 递增间隔；AgentBus 崩溃自愈按重试次数递增延迟（1s→5s）
最大重试次数: AgentBus 事件循环 5 次
```

### 重试流程

```
LLM API 报错 (429/500)
  │
  ├─ 1. litellm num_retries=2（自动，~1s/2s 间隔）
  │
  ├─ 2. AgentBus 事件循环崩溃自愈（最多 5 次，按重试次数递增延迟）
  │     └─ 每次重试推送 step 事件 → 前端显示 "重试中..."
  │
  └─ 3. 如果仍然失败 → SSE error 事件（retryable=true）
        │
        ├─ 前端显示红色错误消息 + ⚠️ 图标 + 重试按钮
        │
        └─ 自动重试倒计时（5s）
              ├─ 倒计时结束 → 自动重新发送
              ├─ 用户点击"取消" → 停止自动重试
              └─ 超过 2 次 → 停止自动重试，只保留手动重试按钮
```

### SSE 断连检测

前端 `sendMessageStream()` 追踪是否收到 `done`/`error` 终端事件：
- 收到终端事件 → 正常结束
- 未收到终端事件就断开 → 抛出 `network` 类型错误，触发重试

### 前端 UI

- **错误消息**：红色边框 + ⚠️ 图标，区别于正常 assistant 消息
- **重试按钮**：仅对 `retryable=true` 的错误显示
- **自动重试横幅**：底部显示倒计时 + 取消按钮

实现路径：
- 后端重试：`backend/app/agent/graph.py` — `_llm_call()` num_retries
- 后端自愈：`backend/app/agent/bus.py` — `run_agent()` 事件循环崩溃重启（递增延迟，最多 5 次）
- 后端分类：`backend/app/api/chat.py` — SSE error 事件携带 retryable/statusCode
- 前端分类：`frontend/src/api/multiAgent.ts` / `frontend/src/stores/multiAgent.ts` — `classifyNetworkError()` + SSE 断连检测
- 前端重试：`frontend/src/stores/multiAgent.ts` — `retryLastMessage()` / `manualRetry()` / `cancelAutoRetry()`（5s 自动倒计时，最多 2 次）
- 前端 UI：`frontend/src/views/MultiAgentView.vue` — 错误样式 + 重试按钮 + 倒计时徽标

---

## 摘要模型配置（可选）

```ini
# .env 可选配置
SUMMARIZATION_MODEL=ollama/qwen2.5:3b     # 摘要用模型（推荐免费方案），不设置则只用截断
SUMMARIZATION_API_KEY=                    # 摘要模型的 API key（可选，不设置则复用 LLM_API_KEY）
SUMMARIZATION_API_BASE=                   # 摘要模型的 API base（可选，不设置则复用 LLM_API_BASE）
SUMMARIZATION_KEEP_MESSAGES=20            # 摘要时保留的最近完整消息数
CHUNK_PAIRS=10                            # 每批摘要的消息对数量（用户+助手为一对）
```

**推荐免费摘要模型：**
| 方案 | 模型 | 说明 |
|------|------|------|
| **本地（推荐）** | `ollama/qwen2.5:3b` | ~1.7GB，中文摘要够用，完全免费，无需 API key |
| 本地 | `ollama/qwen2.5:7b` | ~4.2GB，中文摘要质量更好，但摘要用小模型即可 |
| 免费 API | `gemini/gemini-2.0-flash-lite` | 1500 次/天免费，需配 `SUMMARIZATION_API_KEY` |
| 免费 API | `groq/llama3-8b-8192` | 30 req/min，完全免费，需配 `SUMMARIZATION_API_KEY` |

**运行时切换摘要模型：**

```bash
# 查看当前配置
curl http://localhost:8000/api/config/summarization

# 切换到 Gemini（免费）
curl -X POST http://localhost:8000/api/config/summarization \
  -H "Content-Type: application/json" \
  -d '{"model": "gemini/gemini-2.0-flash-lite"}'

# 切换到 DeepSeek（复用主 LLM）
curl -X POST http://localhost:8000/api/config/summarization \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek/deepseek-v4-flash"}'

# 关闭摘要（仅截断）
curl -X POST http://localhost:8000/api/config/summarization \
  -H "Content-Type: application/json" \
  -d '{"model": ""}'
```

## 生成文件管理

Agent 创建的文档（通过 docx-generator、pdf-generator、excel-generator、kb-export 等插件）自动保存到 `backend/data/generated/`，可在前端 **Generated** 页面管理：

- **列表查看**：按创建时间倒序排列
- **搜索**：按文件名关键字过滤
- **下载**：点击 Download 按钮下载原始文件
- **删除**：点击 Delete 按钮从磁盘删除
- **运行 JS**：`.js` 文件显示"Run"按钮，可在浏览器沙箱中执行（含 mock `fs`/`require`）

---

## 系统监控

可在前端 **Monitoring** 页面查看系统运行统计：

### HTTP 请求统计

| 指标 | 说明 |
|------|------|
| 总请求数 | 服务启动以来的所有 HTTP 请求 |
| 按路径 | 每个 API 端点的调用次数 |
| 按状态码 | 200/404/500 等状态码分布 |

### LLM 调用统计

| 指标 | 说明 |
|------|------|
| 总调用次数 | 包括工具轮次内的每次 LLM 请求 |
| Prompt/Completion Tokens | 累计输入/输出 token 数 |
| 总耗时 / 平均耗时 | 每次 LLM 调用的响应时间 |
| 工具轮数 | 累计 / 平均每次生成的 tool-call 轮次 |
| 按模型 | 不同模型（DeepSeek / GPT / Ollama）的调用分布 |

数据在内存中实时累计，服务重启后重置。实现路径：`backend/app/monitor.py` — `record_request()` / `record_model_call()`

---

## Skills & Plugins

### Skills（技能）

已预装以下技能包，直接输入需求即可自动触发。技能分为两大来源：

- **Anthropic Agent Skills** — 文档处理、设计、开发工具类
- **Matt Pocock Skills** — 软件工程实践、代码质量、需求管理类（来自 [mattpocock/skills](https://github.com/mattpocock/skills)）

#### 📝 文档处理与创作

| Skill | 触发示例 |
|-------|----------|
| **Word 文档** (`docx`) | *"帮我创建一个 Word 文档，内容是产品介绍"* · *"把这个内容导出为 .docx 文件"* · *"帮我写一份报告，格式要好看"* |
| **PPT 演示文稿** (`pptx`) | *"给我做一份 6 页的 PPT，主题是新能源"* · *"把这份大纲变成幻灯片"* · *"帮我美化这个 .pptx 文件"* |
| **PDF 处理** (`pdf`) | *"把这份文档转成 PDF"* · *"提取这个 PDF 中的表格"* · *"合并这几个 PDF 文件"* · *"给 PDF 添加水印"* |
| **Excel 表格** (`xlsx`) | *"创建一个 Excel 表格，包含销售数据"* · *"帮我把这份 CSV 转成 .xlsx"* · *"在这个表格里加个图表"* |

#### 🎨 设计与视觉创作

| Skill | 触发示例 |
|-------|----------|
| **前端界面设计** (`frontend-design`) | *"帮我设计一个产品展示的 Landing Page"* · *"做一个仪表盘风格的页面"* · *"美化这个 React 组件"* |
| **算法艺术** (`algorithmic-art`) | *"用 p5.js 画一个粒子系统动画"* · *"生成一张算法艺术图，流场风格"* · *"创建一个创意编程作品"* |
| **海报/画布设计** (`canvas-design`) | *"帮我设计一张海报，主题是科技论坛"* · *"创建一张艺术画布，输出 PNG"* · *"做一个活动宣传图"* |
| **品牌风格** (`brand-guidelines`) | *"应用 Anthropic 的品牌风格到这个页面"* · *"使用品牌的配色方案"* · *"按品牌规范调整这个设计"* |
| **主题定制** (`theme-factory`) | *"给这份 PPT 应用海洋主题"* · *"帮我生成一个自定义主题，暖色调"* · *"应用 sunset-boulevard 主题"* |

#### 🔧 开发与工具

| Skill | 触发示例 |
|-------|----------|
| **Claude API 开发** (`claude-api`) | *"帮我写一个调用 Claude API 的代码"* · *"给这段代码加上 prompt caching"* · *"从 Claude 3.5 迁移到 Claude 4"* |
| **文档协作编写** (`doc-coauthoring`) | *"帮我写一份技术方案文档"* · *"一起协作写一篇提案"* · *"帮我起草一份设计文档"* |
| **MCP 服务器构建** (`mcp-builder`) | *"创建一个 MCP 服务器，对接 GitHub API"* · *"用 FastMCP 写一个天气查询工具"* · *"帮我构建一个 MCP server"* |
| **Web 应用测试** (`webapp-testing`) | *"帮我测试本地运行的 Web 应用"* · *"用 Playwright 跑一下这个页面的 E2E 测试"* · *"截图看看这个页面长什么样"* |
| **Web Artifacts 构建** (`web-artifacts-builder`) | *"创建一个多组件交互的 HTML Artifact"* · *"用 React + Tailwind 搭建一个复杂的仪表盘"* · *"使用 shadcn/ui 构建这个页面"* |
| **Slack GIF 制作** (`slack-gif-creator`) | *"帮我做一个欢迎新同事的 GIF，用于 Slack"* · *"创建一个产品发布的动画 GIF"* · *"做一个搞笑的 GIF"* |
| **Skill 创建器** (`skill-creator`) | *"帮我创建一个自定义技能"* · *"优化这个技能的触发描述"* · *"测试这个技能的效果"* |

#### ⚙️ 工程实践（Matt Pocock Skills）

这些技能来自 [mattpocock/skills](https://github.com/mattpocock/skills)，专注于软件工程最佳实践。Agent 会根据你的描述自动匹配并加载对应技能。

**需求与规划**

| Skill | 触发示例 | 说明 |
|-------|----------|------|
| **需求追问** (`grill-me`) | *"我想做一个用户管理系统，帮我梳理需求"* · *"帮我把这个想法想清楚"* | 追问式需求分析，适用于无代码库场景 |
| **需求追问+文档** (`grill-with-docs`) | *"帮我梳理这个项目的需求，顺便更新文档"* · *"我想加一个新功能，先帮我理清思路"* | 追问式需求分析 + 自动生成术语表和 ADR |
| **追问核心** (`grilling`) | *"压力测试一下我的方案"* · *"这个设计有没有漏洞"* | 追问原语，逐个问题深挖直到达成共识 |
| **会话交接** (`handoff`) | *"把当前对话总结一下，我要开新会话"* · *"交接一下上下文"* | 生成交接文档，让新会话无缝继续 |
| **对话转规格** (`to-spec`) | *"把我们讨论的内容整理成规格文档"* · *"生成一份 PRD"* | 综合对话上下文，输出结构化规格文档 |
| **规格转票据** (`to-tickets`) | *"把这个规格拆分成开发任务"* · *"拆分成可执行的 ticket"* | 将规格文档拆分为垂直切片的开发票据 |
| **任务实现** (`implement`) | *"按照这个规格开始实现"* · *"实现这个 ticket"* | 按规格/票据实现功能，集成 TDD 和代码审查 |
| **大型项目规划** (`wayfinder`) | *"这个项目太大了，帮我规划一下"* · *"从零开始规划一个大功能"* | 将大型项目拆分为决策票据，逐步推进 |

**代码质量**

| Skill | 触发示例 | 说明 |
|-------|----------|------|
| **测试驱动开发** (`tdd`) | *"用 TDD 方式实现这个功能"* · *"先写测试再实现"* · *"红色-绿色-重构"* | 红-绿循环 TDD，含测试规范和反模式 |
| **代码审查** (`code-review`) | *"帮我审查一下这个分支的代码"* · *"review 一下最近的改动"* · *"检查代码质量"* | 双轴审查：标准（编码规范）+ 规格（需求匹配） |
| **Bug 诊断** (`diagnosing-bugs`) | *"帮我调试这个 bug"* · *"这个 bug 很难复现"* · *"帮我诊断这个问题"* | 6 阶段诊断循环：反馈环 → 复现 → 假设 → 排查 → 修复 → 复盘 |
| **合并冲突解决** (`resolving-merge-conflicts`) | *"帮我解决这个 merge conflict"* · *"rebase 冲突了"* | 系统化解决 Git 合并冲突，保留双方意图 |

**架构设计**

| Skill | 触发示例 | 说明 |
|-------|----------|------|
| **模块设计** (`codebase-design`) | *"这个模块的接口怎么设计"* · *"怎样让代码更可测试"* · *"什么是深度模块"* | 深度模块设计词汇：模块、接口、深度、接缝、适配器 |
| **架构改进** (`improve-codebase-architecture`) | *"帮我看看这个代码库有什么架构问题"* · *"扫描一下有哪些可以改进的地方"* | 扫描代码库，生成 HTML 架构改进报告 |
| **领域建模** (`domain-modeling`) | *"帮我梳理项目的领域术语"* · *"这个概念用什么名字好"* · *"更新一下术语表"* | 建立和维护项目领域词汇表 + ADR |

**研究与学习**

| Skill | 触发示例 | 说明 |
|-------|----------|------|
| **后台研究** (`research`) | *"帮我调研一下 GraphQL 和 REST 的区别"* · *"查一下这个 API 的文档"* | 后台代理研究，输出引用 Markdown 文件 |
| **教学** (`teach`) | *"教我学习 Rust"* · *"帮我系统学习 Docker"* | 多会话教学系统，含课程、学习记录、术语表 |
| **原型验证** (`prototype`) | *"帮我做个原型验证一下这个状态机"* · *"做个 UI 原型看看效果"* | 抛弃型原型：状态逻辑原型 或 UI 多方案对比 |
| **技能编写参考** (`writing-great-skills`) | *"怎么写一个好用的技能"* · *"优化这个技能的描述"* | 技能编写最佳实践参考 |
| **技能路由器** (`ask-matt`) | *"我不知道该用哪个技能"* · *"有什么技能可以用"* | 路由器：根据场景推荐合适的技能 |

**问题分流**

| Skill | 触发示例 | 说明 |
|-------|----------|------|
| **问题分流** (`triage`) | *"帮我看看有哪些 issue 需要处理"* · *"把 #42 标记为 ready-for-agent"* | 问题状态机分流：分类 → 验证 → 追问 → 写 Agent 简报 |
| **技能配置** (`setup-matt-pocock-skills`) | *"配置一下 issue tracker"* · *"设置分流标签"* | 首次使用前配置 issue tracker 和分流标签 |

### Plugins（插件）

预装插件可通过输入需求自动触发：

| Plugin | 工具函数 | 触发示例 |
|--------|----------|----------|
| **example-plugin** | `tool_calculate(expression)` — 计算数学表达式 | *"计算 3.14 * 25 的结果"* · *"算一下 1024 / 8"* |
| | `tool_get_current_time(format)` — 获取当前时间 | *"现在几点了？"* · *"获取当前日期和时间"* |
| | `tool_hello(name)` — 返回问候语 | *"跟张三打个招呼"* |
| **docx-generator** | `tool_create_docx(title, sections)` — 创建 Word 文档 | *"帮我创建一个 Word 文档，内容是产品介绍"* · *"把这段内容导出为 .docx"* |
| **pdf-generator** | `tool_create_pdf(title, sections)` — 创建 PDF 文档（支持中文，内置微软雅黑字体） | *"把这份报告导出为 PDF"* · *"帮我创建一个 PDF 文档"* |
| **excel-generator** | `tool_create_excel(sheets)` — 创建 Excel 表格 | *"创建一个 Excel 表格，包含销售数据"* · *"导出数据为 .xlsx"* |
| **pptx-generator** | `tool_create_pptx(title, slides)` — 创建 PPT 演示文稿 | *"帮我创建一个 PPT，主题是新能源"* · *"把这份大纲变成幻灯片"* |
| **kb-export** | `tool_export_kb_to_docx(query, title)` — 知识库导出为 Word | *"把知识库中关于 XX 的内容导出为 Word"* |
| **filesystem** | `tool_write_file/read_file/ls/grep/glob/edit_file/execute` — 文件操作套件 | Agent 自动用于创建项目、读写文件 |
| **internet-search** | `tool_internet_search(query, max_results, topic)` — 搜索互联网 | *"今天有什么新闻？"* · *"搜索一下 Python 的最新动态"* |
| | `tool_extract_urls(urls, format)` — 按 URL 提取页面内容 | *"打开这篇文章看看"* · *"查看某个网站的具体内容"* |
| **weather** | `tool_get_weather(city, forecast_days)` — 查询天气 | *"今天北京天气怎么样？"* · *"伦敦未来三天的天气预报"* |
| **http-client** | `tool_http_request(method, url, headers, body)` — 发送 HTTP 请求 | *"测试一下 localhost:8000 的 health 接口"* · *"帮我调用这个 API"* |
| | `tool_http_get(url, headers)` — 快捷 GET 请求 | *"GET 请求这个地址"* · *"查看这个 API 的返回"* |
| | `tool_http_post(url, body, headers)` — 快捷 POST 请求 | *"POST 一段 JSON 到这个接口"* · *"提交表单数据"* |

**HTTP 客户端能力与限制：**

| 能力 | 说明 |
|------|------|
| 支持方法 | GET / POST / PUT / DELETE / PATCH / HEAD / OPTIONS |
| 自定义 Headers | Authorization、Content-Type 等任意 header |
| 请求体 | JSON Body、Form 表单（`application/x-www-form-urlencoded`） |
| 目标地址 | 本地接口（`localhost`）、外部 API 均可（受 SSRF 防护限制，见下） |
| SSL 证书 | 跳过验证，支持自签名证书的本地/开发环境 |

| 限制 | 说明 |
|------|------|
| 响应截断 | 超过 5000 字符自动截断，防止 token 爆炸 |
| 不支持文件上传 | 无 `multipart/form-data` 编码 |
| 不支持流式响应 | SSE / chunked 响应一次性读取 |
| 不支持 WebSocket | urllib 不支持 |
| 超时 | 默认 30 秒，可调整 |
| **SSRF 防护** | 所有出站请求经 `check_url` 校验（`app/utils/ssrf.py`）：拦截私网/回环/链路本地/元数据地址（含 `169.254.169.254`）及其解析目标；`tool_execute` 中 curl/wget/ssh/scp/rsync/ping 等网络命令同样校验。本地调试可用 `SSRF_ALLOW_INTERNAL=true` 绕过 |

在聊天框中直接输入需求，Agent 会自动判断需要调用哪些工具来完成你的请求。

**PDF 中文支持**：pdf-generator 插件内置微软雅黑字体（`backend/fonts/msyh.ttc` + `msyhbd.ttc`），支持中文、日文、韩文及常见 Unicode 符号（天气图标、数学符号等）正常显示。

支持两种文件格式：

**格式一：平铺 `.md` 文件**

`backend/skills/*.md`，YAML 头 + Markdown 内容：

```yaml
---
name: my-skill
description: 技能描述
enabled: true
---
技能内容 Markdown ...
```

**格式二：子目录 + `SKILL.md`（兼容 [Anthropic Agent Skills](https://github.com/anthropics/skills) 标准）**

`backend/skills/<skill-name>/SKILL.md`，支持打包脚本、模板、资源文件：

```
backend/skills/
  pdf/
    SKILL.md         # 核心定义（YAML 头 + 指令）
    scripts/         # Python 工具脚本
    reference.md     # 参考文档
    forms.md         # 子主题
    LICENSE.txt
```

两种格式可以混用，`SkillLoader` 在启动时自动检测并加载。目录型 skill 中的脚本/资源文件保持原位，LLM 通过 `load_skill_<name>()` 工具获取 `SKILL.md` 内容后按指令引用。

---

## 权限系统（Permission System）

当 AI Agent 尝试写入工作区之外的路径时（如 `D:\`），系统会通过前端弹窗请求用户授权。

### 路径分级

| 级别 | 行为 | 示例 |
|------|------|------|
| **工作区内** | 静默允许 | `backend/` 下任意路径 |
| **会话工作目录** | 静默允许（本会话内） | 新建会话时选择的工作目录 |
| **系统临时目录** | 静默允许 | `%TEMP%` |
| **系统敏感目录** | 永远拒绝，不弹窗 | `C:\Windows\`, `/etc` |
| **白名单路径** | 静默允许 | `permissions.json` 中记录的路径 |
| **其他外盘路径** | 弹窗询问 | `D:\projects\` |

**会话工作目录**：新建会话时选择的工作目录（opencode `ctx.directory`）在 `classify_path` 中优先于临时目录判定（workspace → 会话目录 → system → temp → …）——即使该目录恰好位于系统临时目录下，也被识别为信任目录而非被 temp 分支"劫持"，其下敏感文件仍走 workspace 分支的保护检查。会话目录为会话级 contextvar 隔离，并发会话互不干扰，会话结束后目录自动失效回落外部路径 `ask`。

**工作目录配置**：可写工作区由前端「工作目录」面板配置（持久化到 `backend/data/runtime_workspaces.json`，运行时生效、无需重启）。路径不落在任何工作区时按 `EXTERNAL_PATH_DEFAULT`（默认 `ask`）处理，审批等待超过 `PERMISSION_APPROVAL_TIMEOUT`（默认 60s）自动拒绝。

**子 Agent 权限桥**：多 Agent 场景下 `code`/`web_search` 子 Agent 复用同一 `PermissionManager`；外部路径触发 `NeedsPermission` 时发出 `permission_request` 事件（SSE 透传给前端共享 `PermissionDialog` 审批面板），随后异步等待用户审批结果：`allowed` 则临时放行并重试工具，`denied`/超时则把拒绝信息反馈 LLM；无事件队列（脱离请求流）时直接拒绝，不会永久阻塞。

### 交互流程

```
AI 调用 tool_write_file("D:\tetris\game.tsx")
  │
  ▼
PermissionManager.check()
  ├─ 工作区内 → 直接执行
  ├─ 白名单中 → 直接执行
  └─ 外部路径 → 抛出 NeedsPermission
       │
       ▼
捕获者 ── 主 Agent: graph.py:_execute_tool / 子 Agent: sub_tools.py:run_tool
  → 创建 PermissionRequest
  → SSE 推送 permission_request 事件
  → 等待用户决策（await_decision，默认 60s 超时）
       │
       ▼
前端 PermissionDialog 弹窗 ───┬── [拒绝] → 返回 "Permission denied"
                              ├── [允许本次] → 临时放行，重试工具
                              └── [允许并记住] → 写入 permissions.json，重试
       │
       ▼
工具执行成功 → LLM 继续生成回答
```

> 子 Agent 无事件队列（如脱离请求流的场景）时直接拒绝而不等待，防止永久阻塞。

### 白名单持久化

用户选择"允许并记住此路径"后，路径会写入 `backend/data/permissions.json`：

```json
{
  "allowed_paths": [
    "D:\\tetris"
  ]
}
```

重启服务后依然生效，无需再次审批。

### 实现文件

| 层 | 文件 | 职责 |
|----|------|------|
| 后端核心 | `backend/app/permission/manager.py` | 路径分类、请求管理、白名单持久化 |
| 后端 API | `backend/app/api/permission.py` | `GET /api/permission/pending` + `POST /.../respond` |
| 工具拦截 | `backend/app/tools/file_tools.py` | 白名单/黑名单/SSRF 校验，`NeedsPermission` 抛出 |
| Agent 集成 | `backend/app/agent/graph.py` | 主 Agent `_execute_tool` 捕获 `NeedsPermission` 并等待决策 |
| 子 Agent 桥 | `backend/app/agent/sub_tools.py` | `code`/`web_search` `run_tool` 权限桥（事件上报 + 等待审批） |
| 前端组件 | `frontend/src/components/PermissionDialog.vue` | 审批弹窗 UI（主/子 Agent 共享） |
| 前端 Store | `frontend/src/stores/permission.ts` | 轮询 + SSE 事件处理 |
| 前端 API | `frontend/src/api/permission.ts` | 请求/响应 API 客户端 |

---

## 性能优化

### 消息列表滚动

聊天消息列表（`frontend/src/views/MultiAgentView.vue`）支持自动滚动与"靠近底部才跟随"策略：

- 新消息/流式增量到达时，若当前视口靠近底部则平滑滚动到底部
- 用户上翻浏览历史时不强制滚动（`onScroll` 检测 `scrollHeight - scrollTop - clientHeight < 100`）

> 旧单 Agent `ChatView.vue` 使用的 `@tanstack/vue-virtual` 虚拟滚动已随单 Agent 模式移除。

实现路径：`frontend/src/views/MultiAgentView.vue` — `onScroll` + `parentRef.scrollTo`（自动滚动跟随）

### BM25 增量索引

上传新文档时，BM25 索引从全量重建优化为增量更新：

| 旧行为 | 新行为 |
|--------|--------|
| 每次上传扫描全部文档重新分词 | 缓存历史词频，只对新文档分词并合并 |
| 复杂度 O(n) | 复杂度 O(新增) |

实现路径：`backend/app/rag/bm25_index.py` — `add()` 方法

### 向量库分页优化

向量库查看页从 2 次 ChromaDB `get()` 调用减少为 1 次：

- 旧：先 `count()` 查总数，再 `get()` 拉数据
- 新：一次 `get()` 获取全部，Python 端切片分页

实现路径：`backend/app/api/vectors.py` — 列表接口

### 并发工具调用

Agent 在每一轮工具调用循环中，所有工具通过 `asyncio.gather()` **并发执行**，而非串行：

- 多工具调用场景下，总延迟从「各工具耗时之和」降为「最慢工具的耗时」
- 文件读写、网络搜索等 IO 密集型工具可并行运行

实现路径：`backend/app/agent/graph.py:259` — `asyncio.gather(*tool_tasks)`

### Shell 命令流式执行

`tool_execute` 使用 `subprocess.Popen` + 线程读取 stdout/stderr，替代 `asyncio.create_subprocess_shell`：

| 旧行为 | 新行为 |
|--------|--------|
| `asyncio.create_subprocess_shell` 在 Windows ProactorEventLoop 偶发失败 | `subprocess.Popen` 全平台稳定 |
| 失败时 fallback 到同步 `subprocess.run`，丢失流式输出 | 始终保持流式输出 |

实现路径：`backend/app/agent/graph.py` — `_execute_tool_streaming()`

### 启动不阻塞

后端启动从同步阻塞改为线程池异步加载，服务在初始化完成前即可响应健康检查：

- `ensure_runtime_state()` 在 `asyncio.to_thread()` 中执行，不阻塞事件循环
- 双检锁防止竞态
- `/health` 在初始化完成前返回 `{"status": "initializing"}`，不 hang

实现路径：`backend/app/runtime.py` — `ensure_runtime_state()` + `_do_init()`

### 上下文管理系统（Context Management）

参考 OpenCode 的 ACP/DCP/DCM 插件生态，实现了一套模块化的上下文管理系统，将上下文控制从「被动截断」升级为「主动策略」。

#### 模块架构

```
backend/app/context/
  ├── __init__.py           # 包入口，导出公共 API
  ├── budget.py             # 上下文预算：usable = max_context - reserve；压缩阈值（0.8 × usable）
  ├── token_counter.py      # tiktoken 精确 token 计数 + fallback + 截断 + 工具消息净化
  ├── tool_output.py        # 工具输出智能边界控制 + 回溯清理
  ├── tool_dedup.py         # 工具结果去重缓存
  └── compaction.py         # 上下文压缩（LLM 锚定摘要 + 轮次尾部保留）
```

#### 1. 上下文预算（budget.py）

对齐 opencode 的 overflow 策略，为单次 LLM 调用划定可用上下文：

| 计算 | 说明 |
|------|------|
| **可用预算 usable** | `max_context_tokens - context_reserve_tokens`（默认 160000 − 8192 = 151808） |
| **压缩阈值** | 显式配置 `COMPACTION_THRESHOLD_TOKENS` > 0 时用配置值；否则自动取 usable 的 60%（默认 ≈ 91084，对齐 opencode） |

预算先于截断兜底生效：长工具循环在触顶之前先触发压缩（总结而非丢弃）。

#### 2. Token 计数（token_counter.py）

| 特性 | 说明 |
|------|------|
| **精确计数** | 使用 tiktoken `cl100k_base` 编码（GPT-3.5/4 系列通用） |
| **Fallback** | tiktoken 不可用时降级为 `len(text) // 4` 启发式估算 |
| **消息级计数** | `estimate_tokens_messages()` 支持文本和多模态消息 |
| **统一截断** | `truncate_messages()` 保留系统提示 + 最新消息，插入截断哨兵；按 `usable_context_tokens()` 对齐预算（调用方无需二次预留） |
| **工具消息净化** | `sanitize_tool_messages()` 在截断/压缩后校正 `tool_calls ↔ tool` 配对：丢弃孤儿工具结果与不完整轮次，同一轮的多条工具结果全量保留，避免向 LLM 发送格式非法的消息序列 |

对比旧实现：

| 指标 | 旧（`len//2`） | 新（tiktoken） |
|------|----------------|----------------|
| 英文误差 | ~2x 过估 | 精确 |
| 中文误差 | ~准确 | 精确 |
| 统一性 | 3 处各自实现 | 单一模块 |

#### 3. 工具输出边界（tool_output.py）

防止大输出（文件读取、shell 命令）撑爆上下文窗口：

| 策略 | 默认值 | 说明 |
|------|--------|------|
| **行数限制** | 200 行 | 超出截断，保留开头 |
| **字节限制** | 32 KB | 超出截断，保留开头 |
| **工具特定限制** | grep: 100 行/16KB | 高产出工具使用更紧的限制 |

截断后附加通知：`[output truncated: showed 200/1500 lines, 32768/98304 bytes]`

**回溯清理（`prune_tool_outputs`）** — 入口边界截断之外的补充策略：当最近 N 轮内累计工具输出超过 `TOOL_OUTPUT_PROTECT_TOKENS`（默认 40000）时，把更早轮次的工具输出替换为剪枝占位符 `[tool output pruned ...]`；仅在收益超过 `TOOL_OUTPUT_PRUNE_MINIMUM_TOKENS`（默认 20000）时执行，避免微小收益的频繁改写。

对比旧实现：

| 旧行为 | 新行为 |
|--------|--------|
| 硬截断 `result[:3000]` 字符 | 按行数 + 字节双重截断 |
| 无截断通知 | 附加 truncation notice |
| 所有工具统一限制 | 按工具类型差异化限制 |
| 只限制新输出 | 新增回溯清理更旧轮次的工具输出 |

#### 4. 工具结果去重（tool_dedup.py）

检测并跳过重复的工具调用，节省 token 和执行时间：

```
LLM 调用 tool_read_file(path="a.py")  → 执行，缓存结果
LLM 再次调用 tool_read_file(path="a.py") → 直接返回缓存，跳过执行
LLM 调用 tool_read_file(path="b.py")  → 新调用，正常执行
```

- 使用 `(tool_name, sorted_args)` 的 MD5 哈希作为去重键
- 缓存作用域为单次 `_generate()` 调用（跨轮次生效）
- 每次调用记录命中率统计（hits/misses/cached_entries）

#### 5. 上下文压缩（compaction.py）

参考 OpenCode 的 compaction 策略，在截断兜底之前先对旧消息做 **LLM 总结压缩**：

| 机制 | 说明 |
|------|------|
| **触发阈值** | `compaction_threshold_tokens()`：显式配置 >0 用配置值；否则自动取 usable 的 80%，保证压缩先于触顶截断 |
| **轮次尾部保留** | 以 user 消息划分轮次，保留最近 `CONTEXT_TAIL_TURNS`（默认 2）轮原文，受 `CONTEXT_PRESERVE_RECENT_TOKENS`（默认 8000）预算约束 |
| **轮次边界切分** | 超预算的轮次在 user 或完整工具轮边界处切开，保证 `tool_calls ↔ tool` 对应关系不被切断；分割后若尾部不以 user 开头，则补上最新问题作为锚定 |
| **锚定摘要** | 若已有 checkpoint，新摘要基于前一份做增量更新（保留仍成立的事实、剔除过时信息、合并新进展），而非从头重写 |
| **结构化模板** | 摘要按 Objective / Important Details / Work State / Next Move / Relevant Files 输出，以 `[Task checkpoint` 标记，与会话持久化压缩基线打通 |
| **失败回退** | 摘要 LLM 调用失败时回退为截断，保留最近消息并插入 `[earlier messages truncated]` 哨兵 |

```
messages 超阈值（如工具输出累积）
  │
  ▼
compactor.should_compact(messages) → True
  │
  ▼
_select(conversation, preserve_recent_tokens)
  ├── 保留最近 tail_turns 轮（受预算约束，按轮次边界切分）
  └── 旧轮 → _summarize(head, previous_summary) → 锚定 checkpoint
        │
        ▼
[system(原有)] + [system checkpoint] + tail（最近轮次原文）
```

#### 集成点

| 模块 | 改动 |
|------|------|
| `agent/graph.py` | `_generate()` 工具循环集成 prune → should_compact → compact → sanitize → truncate（预算对齐）；删除旧 `_estimate_tokens`/`_truncate_messages` |
| `config.py` | 新增 `max_context_tokens` / `context_reserve_tokens` / `compaction_threshold_tokens` / `context_tail_turns` / `context_preserve_recent_tokens` / `tool_output_protect_tokens` / `tool_output_prune_minimum_tokens` |
| `api/chat.py` | `_truncate_history()` 改用 `estimate_tokens`（`MAX_HISTORY_TOKENS`，默认 16000） |
| `middleware/summarization.py` | 删除本地 token 估算，改用 `context.token_counter` |

#### 上下文流转全景

```
[完整历史 SQLite]
    │
    ▼
[API 层: Summarization(可选) / Truncation(16000 tokens)]
    │  ← estimate_tokens() 精确计数
    ▼
[Agent._generate(): 构建 messages]
    │
    ├── [System prompt] (~1000-3500 chars)
    │       └── [RAG context] (按相关性分数排序)
    │
    ├── [History] (API 层预截断)
    ├── [User message]
    │
    ▼
[sanitize_tool_messages(truncate_messages(usable_context_tokens()))]
    │  ← usable = max_context - reserve，预算对齐
    ▼
[LLM 调用 #1]
    │
    ▼
[工具调用循环, 最多 24 轮]
  每轮:
    ├── prune_tool_outputs() 回溯清理更旧轮次的工具输出
    ├── should_compact() → 超阈值则 compact()（锚定摘要 + 尾部保留）
    ├── Dedup 检查 → 命中则跳过执行
    ├── 执行工具（并发）→ bound_tool_output() 智能截断
    ├── sanitize_tool_messages() 净化 tool_calls ↔ tool 配对
    └── truncate_messages(usable) → LLM 调用 #N
        │
        ▼
    [最终回答]
```

实现路径：`backend/app/context/` — 整个包；设计文档见 `docs/context-design.md`

### 工具调用循环与任务状态（graph.py + task_state.py）

工具调用循环位于 `agent/graph.py` 的 `_generate()` 中：内层循环持续「LLM 生成 tool_calls → 并发执行工具 → 结果回填」直到 LLM 不再调用工具，解决「任务未完成就提前终止」的问题。

#### 核心机制

| 机制 | 说明 |
|------|------|
| **工具循环** | 每轮一次完整 LLM 调用，最多 `MAX_TOOL_ROUNDS`（默认 24）；同一轮的多个工具通过 `asyncio.gather()` 并发执行 |
| **强制收尾** | 达到最大轮数仍有 tool_calls 时，执行最后一批工具并注入强制总结 prompt，让 LLM 输出完成报告 |
| **上下文压缩** | 每轮先回溯清理旧工具输出（`prune_tool_outputs`），再检查 token 总量，超阈值（自动 = usable 的 80%）压缩旧消息为锚定 checkpoint（见上文"上下文压缩"） |
| **任务状态持久化** | `task_state.py` 用 SQLite 持久化 step/token/compaction 状态 |
| **工具结果去重** | 相同 `(tool_name, args)` 的调用复用缓存结果 |
| **智能输出边界** | 按行数+字节截断大输出，附带 truncation notice，超阈值回溯清理更旧轮次 |

#### 任务流转

```
用户消息
  │
  ▼
LangGraph 流水线（retrieve → rerank → generate）
  │
  └── _generate 工具循环（最多 24 轮）
        ├── 每轮: prune_tool_outputs → should_compact/compact → dedup → 并发执行工具 → bound_output
        ├── sanitize_tool_messages() 净化 tool_calls ↔ tool 配对
        ├── truncate_messages(usable) → LLM 调用 #N
        └── LLM 返回无 tool_calls → 循环结束，生成最终回答
  │
  ▼
返回 {answer, sources, steps, task}
```

#### 模块架构

```
backend/app/context/
  ├── budget.py            ← 上下文预算与压缩阈值
  ├── token_counter.py     ← tiktoken 精确计数 + 截断 + 工具消息净化
  ├── tool_output.py       ← 智能输出边界 + 回溯清理
  ├── tool_dedup.py        ← 工具结果去重
  ├── compaction.py        ← 上下文压缩（LLM 锚定摘要 + 轮次尾部保留）
  └── task_state.py        ← 任务状态持久化（SQLite）
```

#### 关键参数

| 参数 | 默认值 | 配置方式 |
|------|--------|----------|
| `max_tool_rounds` | 24 | `config.py` / `MAX_TOOL_ROUNDS` 环境变量 |
| `max_context_tokens` | 64,000 | `config.py` / `MAX_CONTEXT_TOKENS` |
| `context_reserve_tokens` | 8,192 | `config.py` / `CONTEXT_RESERVE_TOKENS` |
| `compaction_threshold_tokens` | 0（自动 = 0.8 × usable） | `config.py` / `COMPACTION_THRESHOLD_TOKENS` |
| `context_tail_turns` | 2 | `config.py` / `CONTEXT_TAIL_TURNS` |
| `context_preserve_recent_tokens` | 8,000 | `config.py` / `CONTEXT_PRESERVE_RECENT_TOKENS` |
| `tool_output_protect_tokens` | 40,000 | `config.py` / `TOOL_OUTPUT_PROTECT_TOKENS` |
| `tool_output_prune_minimum_tokens` | 20,000 | `config.py` / `TOOL_OUTPUT_PRUNE_MINIMUM_TOKENS` |

#### 前端适配

| 文件 | 改动 |
|------|------|
| `views/MultiAgentView.vue` | 步骤面板按 `agent_step` 事件 upsert（`step_id` 对齐） |
| `types/index.ts` | `SSEEvent` 加 `task` 字段（task_id/status/step/total_tokens/tool_calls_count） |
| compaction `step_end` 事件 | 包含 `detail`："X 条消息压缩为 Y 条" |

实现路径：`backend/app/agent/graph.py` — `_generate()` 工具循环（压缩/去重/输出边界）
`backend/app/context/compaction.py` — 上下文压缩
`backend/app/context/task_state.py` — 任务状态持久化

---

## 项目学习指南

### 1. 入口层 — 了解整体启动流程

```
backend/main.py          → FastAPI 应用入口，lifespan 启动流程
backend/app/runtime.py   → 运行时初始化（环境变量、向量库、嵌入模型、Agent）
backend/app/config.py    → 配置项（读 .env）
```

### 2. Agent 核心 — 理解 AI 对话链路

```
backend/app/agent/graph.py    ← ⭐ 最核心：LangGraph 工作流（retrieve → rerank → generate）
backend/app/agent/tools.py    → 工具定义 + 系统 prompt
backend/app/agent/memory.py   → 共享记忆（持久化到 data/agent_memory.json）
backend/app/agent/sub_tools.py → 子 Agent（code/web_search）工具链 + 权限桥
backend/app/agent/supervisor.py → 多 Agent 路由编排（whitelist 校验 + 子任务超时）
```

`graph.py` 是整个大脑，LLM 如何调用、工具如何执行、流式响应如何产生，全在这里。

### 3. 上下文管理 — 理解 token 控制策略

```
backend/app/context/budget.py         → 上下文预算 + 压缩阈值（0.8 × usable）
backend/app/context/token_counter.py  → tiktoken 精确计数 + 截断 + 工具消息净化
backend/app/context/tool_output.py    → 工具输出智能边界控制 + 回溯清理
backend/app/context/tool_dedup.py     → 工具结果去重缓存
backend/app/context/compaction.py     → 上下文压缩（LLM 锚定摘要 + 轮次尾部保留）
backend/app/context/task_state.py     → 任务状态持久化（SQLite）
```

### 4. RAG 检索链路 — 理解知识库如何工作

```
backend/app/rag/document_processor.py  → 文档上传后如何分块（章节感知）
backend/app/rag/embeddings.py          → 文本如何转成向量
backend/app/rag/vector_store.py        → ChromaDB 向量存储
backend/app/rag/retriever.py           ← ⭐ 检索核心：混合检索 + RRF 融合
backend/app/rag/bm25_index.py          → BM25 关键词检索
backend/app/rag/reranker.py            → 重排序提升精度
backend/app/rag/intent.py              → 意图识别（章节查询跳过向量检索）
backend/app/rag/chapter_store.py       → 章节元数据存储
```

### 5. API 层 — 前后端如何交互

```
backend/app/api/chat.py       ← ⭐ 聊天接口（SSE 流式）
backend/app/api/documents.py  → 文档上传 + 异步任务
```

### 6. 插件系统 — 理解扩展机制

```
backend/app/plugins/loader.py   → 插件如何加载（扫描 tool_* 函数）
backend/app/skills/loader.py    → Skill 如何加载（Markdown 文件）
backend/plugins/example_plugin.py → 最简单的插件示例
```

### 7. 前端 — Vue 3 SPA

```
frontend/src/stores/multiAgent.ts  ← ⭐ 状态管理（会话、消息、SSE 流式接收、重试逻辑、目录绑定）
frontend/src/views/MultiAgentView.vue → ⭐ 唯一聊天界面（并行 Agent 面板、Vector DB 开关、工作目录、重试按钮）
frontend/src/views/               → 其余：Documents / Skills / Plugins / Vectors / GeneratedFiles / Monitoring / Login / NotFound / CustomTools
frontend/src/api/multiAgent.ts     → 多 Agent 流式调用（sendMultiAgentStream + classifyNetworkError）
frontend/src/api/sessions.ts       → /api/sessions REST client（CRUD + 列表/详情/删除，含 kind 过滤）
frontend/src/api/session-cache.ts  → IndexedDB 会话缓存（双重持久化）
frontend/src/api/auth.ts           → 用户身份工具（ensureAuth/注册/换 token，可选签名校验）
frontend/src/config/models.ts      → SUPPORTED_MODELS（模型列表，store + view 共享）
frontend/src/components/           → 各组件（Sidebar、MultiAgentChatHistory、AgentPanel、PermissionDialog、ChatInput 等）
frontend/src/types/index.ts        → 类型定义（MultiAgentMessage、ChatError、MultiAgentSSEEvent）
```

> 单 Agent 相关前端文件（`stores/chat.ts`、`stores/mobileChat.ts`、`api/chat.ts`、`views/ChatView.vue`、`views/MobileView.vue`、`components/ChatHistory.vue`/`ChatMessage.vue`/`StepTaskList.vue`、`src/mobile/`）均已删除。

### 核心数据流

```
用户输入
  → 前端 multiAgent.ts 发送 POST /api/chat/multi-agent/stream
    → backend api/chat.py 接收（_agent_semaphore 排队）
      → supervisor 路由 → AgentBus 派发到 rag/web_search/code 子 Agent
        → agent/graph.py LangGraph 编排（retrieve → rerank → generate）
          │
          ├─ _retrieve()  → retriever.py 混合检索
          ├─ _rerank()    → reranker.py 重排序
          └─ _generate()  → litellm 调 LLM + 工具循环（最多 MAX_TOOL_ROUNDS 轮）
                ├─ prune_tool_outputs: 回溯清理更旧工具输出
                ├─ compaction: 超阈值（0.8 × usable）自动压缩旧消息
                ├─ dedup: 相同工具调用复用缓存
                ├─ bound_output: 大输出智能截断
                └─ sanitize + truncate(usable) → 下一轮 LLM 调用
      → SSE 事件流返回（queued → routing → agent_start/agent_stream/agent_done/agent_error → done）
    → 前端 MultiAgentView.vue 流式渲染（按 agent_id 分面板）
```

### 推荐学习顺序

| 优先级 | 模块 | 原因 |
|--------|------|------|
| **1** | `agent/graph.py` | 核心链路，理解 Agent 如何思考和执行 |
| **2** | `context/budget.py` + `context/compaction.py` | 上下文预算与压缩策略，理解长工具循环如何不爆上下文 |
| **3** | `rag/retriever.py` + `vector_store.py` | RAG 是项目的核心价值 |
| **4** | `context/token_counter.py` | 精确 token 计数 + 工具消息净化 |
| **5** | `api/chat.py` | 理解前后端如何通过 SSE 流式通信（multi-agent 唯一入口） |
| **6** | `agent/supervisor.py` | 多 Agent 路由与分解逻辑 |
| **7** | `stores/multiAgent.ts` | 前端状态管理 + 会话隔离 + 重试 |
| **8** | `plugins/loader.py` | 理解插件扩展机制 |

先跑通 `graph.py` 的工作流，再看上下文预算/压缩（`budget.py` + `compaction.py`），然后向外扩展到 RAG、Supervisor、API、前端，最后看插件系统。
