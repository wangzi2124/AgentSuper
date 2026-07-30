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
| **会话持久化** | IndexedDB 本地缓存 + 服务器 SQLite 双重持久化，页面刷新/SSE 中断不丢失消息 |
| **错误重试机制** | 三层重试架构：litellm 内置重试 → TaskRunner 指数退避 → 前端自动重试倒计时 + 手动重试按钮 |
| **并发控制** | 后端 Semaphore 限制同时运行的 Agent 任务数（默认 2），超出自动排队，前端实时显示排队/流式状态 |
| **Skills（技能）** | Markdown 文件定义技能，动态加载，可在 Web 界面启用/禁用 |
| **Plugins（插件）** | Python 文件定义 tool_* 函数（如搜索、天气、生成文档），Agent 按需调用 |
| **HTTP 客户端** | Agent 可直接发起 HTTP 请求测试 API 接口（GET/POST/PUT/DELETE），支持自定义 headers 和 body |
| **Vector DB 开关** | 用户可在聊天界面手动控制是否启用向量库检索，关闭后 Agent 仅凭自身知识回答 |
| **生成文件管理** | Agent 创建的文档（.docx/.pdf/.xlsx/.pptx）可在独立页面查看、搜索、下载和删除，PDF 支持中文显示 |
| **本地 Embedding** | 使用 sentence-transformers 本地运行，通过 ModelScope 下载模型 |
| **检索重排序** | Cross-encoder 对检索结果重打分（top-3），显著提升回答精度 |
| **上下文管理** | tiktoken 精确 token 计数 + 工具输出智能边界控制 + 工具结果去重，防止 context 膨胀 |
| **对话持久化** | SQLite 存储对话历史，服务重启不丢失 |
| **来源引用** | 回答时标注检索到的文档来源及相似度分数 |
| **系统监控** | 请求级日志（方法/路径/状态/耗时）+ LLM 调用统计（模型/token/耗时/工具轮数），Web 页面可视化展示 |
| **虚拟滚动** | 聊天消息列表使用 `@tanstack/vue-virtual`，只渲染可视区域节点，长对话 DOM 不臃肿 |
| **权限系统** | AI Agent 写外部路径时前端弹窗审批，支持白名单持久化 |
| **任务执行引擎** | 参考 OpenCode 双层循环架构，任务持续执行直到完成，支持上下文压缩和最大步数限制 |
| **上下文压缩** | 消息超 80K tokens 时自动压缩旧消息为结构化 checkpoint，保留关键工作状态 |

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
| **文档解析** | pypdf（PDF）, 原生文本解析（TXT/MD） |
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
```

### 切换 LLM 提供商

| 提供商 | LLM_API_BASE | LLM_MODEL 示例 |
|--------|-------------|----------------|
| DeepSeek | `https://api.deepseek.com` | `deepseek/deepseek-v4-flash` / `deepseek/deepseek-v4-pro` |
| OpenAI | 留空 | `gpt-4o` / `gpt-4o-mini` |
| Ollama（本地） | `http://localhost:11434` | `ollama/qwen2.5:7b` |

---

## 用户身份认证

所有 API 请求通过 `X-User-Id` 请求头标识用户身份，未携带时后端默认使用 `"anonymous"`。

```javascript
fetch("http://localhost:8000/api/chat/", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-User-Id": "user_xxx",  // ← 用户身份
  },
  body: JSON.stringify({ message: "你好" }),
})
```

| 文件 | 作用 |
|------|------|
| `frontend/src/api/auth.ts` | `getUserId()`/`setUserId()` — 读写 localStorage，默认 `"anonymous"` |
| `frontend/src/api/fetch.ts` | `addAuthHeaders()` 自动注入 `X-User-Id`；`fetchWithTimeout` 封装自动带认证头 |
| `frontend/src/mobile/SettingsPanel.vue` | 手机端设置面板可查看/编辑用户身份 |

后端通过 `_get_user_id(request)` 统一提取，接入 JWT/OAuth 后只需修改该函数，前端接口不变。

### 会话类型隔离

聊天对话按类型隔离存储，不同前端会话列表互不干扰：

| 类型 | 前端页面 | conv_type 值 |
|------|----------|-------------|
| **单 Agent 聊天** | ChatView、MobileView | `chat` |
| **多 Agent 编排** | MultiAgentView | `multi-agent` |

后端 `conversations` 表新增 `type` 列，`GET /api/chat/conversations` 支持 `?conv_type=` 参数过滤。三个前端入口各自只加载自己类型的会话。

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查 |
| POST | `/api/chat/` | 发送聊天消息（单 Agent） |
| POST | `/api/chat/stream` | 流式聊天 SSE（单 Agent），支持 queued/step_start/step_end/done 事件 |
| POST | `/api/chat/multi-agent/` | 发送聊天消息（多 Agent Supervisor） |
| POST | `/api/chat/multi-agent/stream` | 流式聊天 SSE（多 Agent），支持 routing/agent_start/agent_stream/agent_done/done 事件 |
| GET | `/api/chat/stream/status` | 查询并发状态（active/queue_depth） |
| GET | `/api/chat/conversations?conv_type=chat\|multi-agent` | 按类型过滤会话列表 |
| GET | `/api/chat/conversations/:id?conv_type=` | 获取指定类型会话详情 |
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

1. **自动生成 ID**：首次对话不传 `conversation_id`，后端自动生成并返回
2. **历史加载**：传 `conversation_id` 则从 SQLite 恢复完整历史
3. **滑动窗口截断**：`_truncate_history()` 限制 4000 tokens，超出的最早消息被丢弃，插入 `[earlier history truncated]` 占位
4. **摘要压缩（可选，分层）**：配置 `SUMMARIZATION_MODEL` 后在截断前先尝试用 LLM 压缩早期消息。旧消息按 `CHUNK_PAIRS`（默认 10 对）分批，每批独立压缩为子摘要，若子摘要合并后仍超阈值则递归合并，直到适应预算。保留最近的 `SUMMARIZATION_KEEP_MESSAGES`（默认 20）条完整消息。摘要失败时自动回退为截断。
5. **持久化**：每次回答后，user/assistant 消息对追加写入 `data/conversations.db`

> 截断在 LLM 调用前发生，仅影响 prompt 传入的历史，不影响数据库中的完整记录。

实现路径：`backend/app/api/chat.py` — `_load_conversation` / `_truncate_history` / `_save_conversation`，`backend/app/middleware/summarization.py` — `HierarchicalSummarizationMiddleware`

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
- Store 集成：`frontend/src/stores/chat.ts` — `loadConversation()` / `persistSession()`
- 后端持久化：`backend/app/api/chat.py` — `done` 事件时保存 `sources`/`steps` 到 SQLite

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

实现路径：`frontend/src/stores/chat.ts` — `useChatStore`（按会话ID存储消息，`conv_type='chat'`）
- 多 Agent 版本：`frontend/src/stores/multiAgent.ts` — `useMultiAgentStore`（支持 agents 面板和 routing 状态，`conv_type='multi-agent'`）
- 手机版本：`frontend/src/stores/mobileChat.ts` — `useMobileChatStore`（移动端适配，`conv_type='chat'`）

### 并发控制

后端使用 `asyncio.Semaphore(2)` 限制同时运行的 Agent 任务数，防止多会话同时 streaming 导致资源争抢：

```
Session A 发消息 → 获取 slot #1 → 开始执行
Session B 发消息 → 获取 slot #2 → 开始执行
Session C 发消息 → slot 已满 → 排队等待
    │
    ├─ SSE 事件: {type: "queued", queue_position: 1}
    ├─ 前 Sidebar 显示 "⏳ #1"
    ├─ ChatView Header 显示 "排队中 #1"
    │
    ▼
Session A 完成 → slot #1 释放 → Session C 获得 slot
    │
    ├─ SSE 事件: {type: "step_start"} → 进入 running 阶段
    ├─ Sidebar 显示 "● streaming"
    └─ 开始流式输出
```

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `MAX_CONCURRENT_AGENTS` | 2 | 同时运行的最大 Agent 任务数 |

状态 API：`GET /api/chat/stream/status` 返回 `{max_concurrent, active, queue_depth}`

实现路径：
- 后端：`backend/app/api/chat.py` — `chat_stream()` 中 `_get_agent_semaphore()` + `run_agent()` 的 `async with sem`
- 前端状态：`frontend/src/stores/chat.ts` — `SessionState.streamPhase` / `queuePosition`
- 前端显示：`frontend/src/components/ChatHistory.vue`（Sidebar 标签）、`frontend/src/views/ChatView.vue`（Header 标签）

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
│  litellm num_retries=2 + TaskRunner 指数退避(3次)    │
└─────────────────────────────────────────────────────┘
```

### 错误分类

| 错误类型 | 分类依据 | 可重试 | 后端重试 | 前端自动重试 |
|----------|----------|--------|----------|-------------|
| 429 Rate Limit | `RateLimitError` / `429` | ✅ | litellm 2次 + TaskRunner 3次 | 5s 倒计时，最多 2 次 |
| 500/502/503 | `InternalServerError` / `5xx` | ✅ | litellm 2次 + TaskRunner 3次 | 5s 倒计时，最多 2 次 |
| 网络断开 | `Failed to fetch` / SSE 断连 | ✅ | — | 5s 倒计时，最多 2 次 |
| 超时 | `timeout` / `timed out` | ✅ | litellm 2次 | 5s 倒计时，最多 2 次 |
| Context Overflow | `context_length_exceeded` | ❌ | — | — |
| 401/403 | 认证失败 | ❌ | — | — |
| 用户取消 | `AbortError` | ❌ | — | — |

### 退避策略

```
retryAfter header → 使用 header 值（上限 60s）
无 header → 2s → 4s → 8s（上限 30s）
最大重试次数: 3
```

### 重试流程

```
LLM API 报错 (429/500)
  │
  ├─ 1. litellm num_retries=2（自动，~1s/2s 间隔）
  │
  ├─ 2. TaskRunner 指数退避（2s→4s→8s，最多 3 次）
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
- 后端重试：`backend/app/context/task_runner.py` — `_run_loop()` 指数退避
- 后端分类：`backend/app/api/chat.py` — SSE error 事件携带 retryable/statusCode
- 前端分类：`frontend/src/api/chat.ts` — `classifyNetworkError()` + SSE 断连检测
- 前端重试：`frontend/src/stores/chat.ts` — `retryLastMessage()` / `manualRetry()` / `cancelAutoRetry()`
- 前端 UI：`frontend/src/components/ChatMessage.vue` — 错误样式 + 重试按钮

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
| 目标地址 | 本地接口（`localhost`）、外部 API 均可 |
| SSL 证书 | 跳过验证，支持自签名证书的本地/开发环境 |

| 限制 | 说明 |
|------|------|
| 响应截断 | 超过 5000 字符自动截断，防止 token 爆炸 |
| 不支持文件上传 | 无 `multipart/form-data` 编码 |
| 不支持流式响应 | SSE / chunked 响应一次性读取 |
| 不支持 WebSocket | urllib 不支持 |
| 超时 | 默认 30 秒，可调整 |

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
| **系统临时目录** | 静默允许 | `%TEMP%` |
| **系统敏感目录** | 永远拒绝，不弹窗 | `C:\Windows\`, `/etc` |
| **白名单路径** | 静默允许 | `permissions.json` 中记录的路径 |
| **其他外盘路径** | 弹窗询问 | `D:\projects\` |

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
graph.py 捕获 → 创建 PermissionRequest
  → SSE 推送 permission_request 事件
  → 等待用户决策
       │
       ▼
前端 PermissionDialog 弹窗 ───┬── [拒绝] → 返回 "Permission denied"
                              ├── [允许本次] → 临时放行，重试工具
                              └── [允许并记住] → 写入 permissions.json，重试
       │
       ▼
工具执行成功 → LLM 继续生成回答
```

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
| 工具拦截 | `backend/plugins/filesystem.py` | `_ensure_safe` 集成权限检查 |
| Agent 集成 | `backend/app/agent/graph.py` | `_execute_tool` 捕获 `NeedsPermission` 并等待决策 |
| 前端组件 | `frontend/src/components/PermissionDialog.vue` | 审批弹窗 UI |
| 前端 Store | `frontend/src/stores/permission.ts` | 轮询 + SSE 事件处理 |
| 前端 API | `frontend/src/api/permission.ts` | 请求/响应 API 客户端 |

---

## 性能优化

### 虚拟滚动（Virtual Scrolling）

聊天消息列表使用 `@tanstack/vue-virtual` 实现虚拟滚动，只渲染可视区域 ±5 条的 DOM 节点：

- 长对话（数百条消息）DOM 节点数保持恒定，不再线性增长
- 消息高度动态估算（基于内容长度 + sources + 附件），首次渲染后自动校正
- 自动检测是否靠近底部，新消息到达时仅在靠近底部时自动滚动

实现路径：`frontend/src/views/ChatView.vue` — `useVirtualizer({ estimateSize, overscan: 5 })`

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
  ├── token_counter.py      # tiktoken 精确 token 计数 + fallback
  ├── tool_output.py        # 工具输出智能边界控制
  └── tool_dedup.py         # 工具结果去重缓存
```

#### 1. Token 计数（token_counter.py）

| 特性 | 说明 |
|------|------|
| **精确计数** | 使用 tiktoken `cl100k_base` 编码（GPT-3.5/4 系列通用） |
| **Fallback** | tiktoken 不可用时降级为 `len(text) // 4` 启发式估算 |
| **消息级计数** | `estimate_tokens_messages()` 支持文本和多模态消息 |
| **统一截断** | `truncate_messages()` 保留系统提示 + 最新消息，插入截断哨兵 |

对比旧实现：

| 指标 | 旧（`len//2`） | 新（tiktoken） |
|------|----------------|----------------|
| 英文误差 | ~2x 过估 | 精确 |
| 中文误差 | ~准确 | 精确 |
| 统一性 | 3 处各自实现 | 单一模块 |

#### 2. 工具输出边界（tool_output.py）

防止大输出（文件读取、shell 命令）撑爆上下文窗口：

| 策略 | 默认值 | 说明 |
|------|--------|------|
| **行数限制** | 200 行 | 超出截断，保留开头 |
| **字节限制** | 32 KB | 超出截断，保留开头 |
| **工具特定限制** | grep: 100 行/16KB | 高产出工具使用更紧的限制 |

截断后附加通知：`[output truncated: showed 200/1500 lines, 32768/98304 bytes]`

对比旧实现：

| 旧行为 | 新行为 |
|--------|--------|
| 硬截断 `result[:3000]` 字符 | 按行数 + 字节双重截断 |
| 无截断通知 | 附加 truncation notice |
| 所有工具统一限制 | 按工具类型差异化限制 |

#### 3. 工具结果去重（tool_dedup.py）

检测并跳过重复的工具调用，节省 token 和执行时间：

```
LLM 调用 tool_read_file(path="a.py")  → 执行，缓存结果
LLM 再次调用 tool_read_file(path="a.py") → 直接返回缓存，跳过执行
LLM 调用 tool_read_file(path="b.py")  → 新调用，正常执行
```

- 使用 `(tool_name, sorted_args)` 的 MD5 哈希作为去重键
- 缓存作用域为单次 `_generate()` 调用（跨轮次生效）
- 每次调用记录命中率统计（hits/misses/cached_entries）

#### 集成点

| 模块 | 改动 |
|------|------|
| `agent/graph.py` | `_generate()` 中工具循环集成去重 + 输出边界；删除旧 `_estimate_tokens`/`_truncate_messages` |
| `api/chat.py` | `_truncate_history()` 改用 `estimate_tokens` |
| `middleware/summarization.py` | 删除本地 token 估算，改用 `context.token_counter` |

#### 上下文流转全景

```
[完整历史 SQLite]
    │
    ▼
[API 层: Summarization(可选) / Truncation(4000 tokens)]
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
[truncate_messages() 安全网] ← 1M tokens
    │
    ▼
[LLM 调用 #1]
    │
    ▼
[工具调用循环, 最多 20 轮]
  每轮:
    ├── Dedup 检查 → 命中则跳过执行
    ├── 执行工具 → bound_tool_output() 智能截断
    ├── 结果存入 early_results (按 tool_call_id 索引)
    └── truncate_messages() → LLM 调用 #N
        │
        ▼
    [最终回答]
```

实现路径：`backend/app/context/` — 整个包

### 任务执行引擎（Task Runner）

参考 OpenCode 的双层 while 循环架构，解决「任务未完成就提前终止」的问题。

#### 核心设计

| 机制 | 说明 |
|------|------|
| **双层循环** | 内层：LLM + 工具调用持续到 LLM 不再返回 tool_calls；外层：检查是否有用户追加输入 |
| **最大步数** | 默认 50 步，超限后注入强制总结 prompt，让 LLM 输出完成报告后结束 |
| **上下文压缩** | 每步检查 token 总量，超 80K 自动压缩旧消息为结构化 checkpoint |
| **任务状态持久化** | SQLite 持久化 step/token/compaction 状态，支持崩溃恢复 |
| **工具结果去重** | 相同 `(tool_name, args)` 的调用复用缓存结果 |
| **智能输出边界** | 按行数+字节截断大输出，附带 truncation notice |

#### 任务流转

```
用户消息
  │
  ▼
TaskRunner.run()  ← 创建 TaskState，持久化到 SQLite
  │
  ├── Phase 1: LangGraph RAG 流水线（retrieve → rerank → generate）
  │     └── _generate 内层循环（最多 50 轮工具调用）
  │           ├── 每轮: compaction 检查 → dedup → bound_output
  │           └── LLM 返回无 tool_calls → 内层结束
  │
  ├── Phase 2: 检查 LLM 是否还想继续
  │     ├── 有 tool_calls → 继续循环（最多 50 步）
  │     │     ├── compaction: 压缩旧消息
  │     │     ├── 工具执行: dedup + bound_output
  │     │     └── 记录 token/step 到 SQLite
  │     └── 无 tool_calls → 任务完成
  │
  └── TaskState.mark_completed()
        │
        ▼
      返回 {answer, sources, steps, task}
```

#### 对比旧架构

| 维度 | 旧架构 | 新架构（Task Runner） |
|------|--------|---------------------|
| 执行模型 | 单次 invoke，最多 20 轮工具调用 | 双层循环，最多 50 步 |
| 任务完成判定 | `rounds >= 20` 或空内容 → 强制结束 | LLM 不再返回 tool_calls → 自然结束 |
| 上下文膨胀 | 被动截断（1M tokens 丢弃旧消息） | 主动压缩（80K tokens 时 LLM 总结） |
| 状态持久化 | 无 | SQLite 持久化 step/token/compaction |
| 崩溃恢复 | 不支持 | 可从 SQLite 恢复任务状态 |

#### 模块架构

```
backend/app/context/
  ├── token_counter.py     ← tiktoken 精确计数
  ├── tool_output.py       ← 智能输出边界
  ├── tool_dedup.py        ← 工具结果去重
  ├── compaction.py        ← 上下文压缩（LLM 总结）
  ├── task_state.py        ← 任务状态持久化（SQLite）
  └── task_runner.py       ← 核心执行引擎（双层循环）
```

#### 关键参数

| 参数 | 默认值 | 配置方式 |
|------|--------|----------|
| `MAX_STEPS` | 50 | `task_runner.py` 常量 |
| `COMPACTION_THRESHOLD` | 80,000 tokens | `task_runner.py` 常量 |
| `keep_recent` | 6 条 | `compaction.py` 常量 |
| `max_tool_rounds` | 50 | `graph.py` 常量 |

#### 前端适配

| 文件 | 改动 |
|------|------|
| `StepTaskList.vue` | `stepOrder` 加入 `compaction`，排在 `rerank` 和 `generate` 之间 |
| `types/index.ts` | `SSEEvent` 加 `task` 字段（task_id/status/step/total_tokens/tool_calls_count） |
| compaction `step_end` 事件 | 包含 `detail`："X 条消息压缩为 Y 条" |

实现路径：`backend/app/context/task_runner.py` — 核心引擎
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
```

`graph.py` 是整个大脑，LLM 如何调用、工具如何执行、流式响应如何产生，全在这里。

### 3. 上下文管理 — 理解 token 控制策略

```
backend/app/context/token_counter.py  → tiktoken 精确计数 + 截断策略
backend/app/context/tool_output.py    → 工具输出智能边界控制
backend/app/context/tool_dedup.py     → 工具结果去重缓存
backend/app/context/compaction.py     → 上下文压缩（LLM 总结旧消息）
backend/app/context/task_state.py     → 任务状态持久化（SQLite）
backend/app/context/task_runner.py    ← ⭐ 核心：双层循环任务执行引擎
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
frontend/src/stores/chat.ts        ← ⭐ 状态管理（会话、消息、SSE 流式接收、重试逻辑）
frontend/src/stores/multiAgent.ts  → 多 Agent 状态管理（agents 面板、routing 状态）
frontend/src/stores/mobileChat.ts  → 手机端状态管理
frontend/src/views/ChatView.vue    → 聊天主界面
frontend/src/views/MultiAgentView.vue → 多 Agent 聊天界面（并行 Agent 面板）
frontend/src/views/MobileView.vue  → 手机端聊天界面
frontend/src/api/chat.ts           → 聊天 API 调用 + conv_type 过滤
frontend/src/api/multiAgent.ts     → 多 Agent API（sendMultiAgentStream + conv_type）
frontend/src/api/session-cache.ts  → IndexedDB 会话缓存（双重持久化）
frontend/src/api/auth.ts           → 用户身份工具（getUserId/setUserId）
frontend/src/components/           → 各组件（Sidebar、ChatMessage、MultiAgentResponse 等）
frontend/src/types/index.ts        → 类型定义（Message、ChatError、SSEEvent、MultiAgentSSEEvent）
```

### 核心数据流

```
用户输入
  → 前端 chat.ts 发送 POST /api/chat/stream
    → backend api/chat.py 接收
      → TaskRunner.run()  ← 任务执行引擎
        │
        ├── Phase 1: agent/graph.py LangGraph 编排
        │     ├─ _retrieve()  → retriever.py 混合检索
        │     ├─ _rerank()    → reranker.py 重排序
        │     └─ _generate()  → litellm 调 LLM + 工具循环（最多 50 轮）
        │
        ├── Phase 2: 持续循环（直到 LLM 不再调用工具）
        │     ├─ compaction: 超 80K tokens 自动压缩
        │     ├─ dedup: 相同工具调用复用缓存
        │     └─ bound_output: 大输出智能截断
        │
        └── TaskState → SQLite 持久化
      → SSE 事件流返回
    → 前端 ChatView.vue 流式渲染
```

### 推荐学习顺序

| 优先级 | 模块 | 原因 |
|--------|------|------|
| **1** | `agent/graph.py` | 核心链路，理解 Agent 如何思考和执行 |
| **2** | `context/task_runner.py` | 任务执行引擎，理解双层循环如何保证任务完成 |
| **3** | `rag/retriever.py` + `vector_store.py` | RAG 是项目的核心价值 |
| **4** | `context/compaction.py` | 理解上下文压缩策略 |
| **5** | `api/chat.py` | 理解前后端如何通过 SSE 流式通信 |
| **6** | `stores/chat.ts` | 前端状态管理 + 会话隔离 |
| **7** | `plugins/loader.py` | 理解插件扩展机制 |

先跑通 `graph.py` 的工作流，再看 `task_runner.py` 的双层循环，然后向外扩展到 RAG、API、前端，最后看插件系统。
