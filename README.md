# Knowledge Base System — RAG + AI Agent

基于 RAG（检索增强生成）的知识库 AI 问答系统，支持文档上传、向量检索、多模型对话、Skills 和 Plugins 动态扩展。

---

## 功能

| 功能 | 说明 |
|------|------|
| **智能问答** | 上传文档后，通过 RAG 检索相关内容，结合 LLM 生成精准回答 |
| **多模型支持** | 前端下拉菜单切换 DeepSeek V3 / R1、OpenAI GPT-4o / 4o-mini |
| **文档管理** | 支持 TXT / MD / PDF 上传，自动**章节感知分块**、向量化存储到 ChromaDB |
| **混合检索** | 向量检索 + BM25 关键词检索融合（RRF 排序），精确匹配与语义搜索兼顾 |
| **结构化章节检索** | 用户问题命中章节关键词（如"第一章"）时，**跳过向量检索**，直接查章节元数据表返回精确结果 |
| **父子文档结构** | 分块时自动生成父文档（章节标题+摘要）和子文档（正文块），检索子文档时携带父文档标题注入 LLM 上下文 |
| **中文支持** | 内置 `BAAI/bge-small-zh-v1.5` 中文嵌入模型，准确理解中文语义 |
| **上传进度** | 上传过程实时显示进度条（0-100%）及阶段描述（上传→分块→嵌入→入库） |
| **多轮对话** | 自动生成 conversation_id，支持同一会话内的上下文连续对话 |
| **Skills（技能）** | Markdown 文件定义技能，动态加载，可在 Web 界面启用/禁用 |
| **Plugins（插件）** | Python 文件定义 tool_* 函数（如搜索、天气、生成文档），Agent 按需调用 |
| **Vector DB 开关** | 用户可在聊天界面手动控制是否启用向量库检索，关闭后 Agent 仅凭自身知识回答 |
| **生成文件管理** | Agent 创建的 .docx 文件可在独立页面查看、搜索文件名、下载和删除 |
| **本地 Embedding** | 使用 sentence-transformers 本地运行，通过 ModelScope 下载模型 |
| **检索重排序** | Cross-encoder 对检索结果重打分（top-3），显著提升回答精度 |
| **上下文管理** | 滑动窗口截断历史（4000 tokens），防止 context 溢出 |
| **对话持久化** | SQLite 存储对话历史，服务重启不丢失 |
| **来源引用** | 回答时标注检索到的文档来源及相似度分数 |
| **系统监控** | 请求级日志（方法/路径/状态/耗时）+ LLM 调用统计（模型/token/耗时/工具轮数），Web 页面可视化展示 |

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
| **文档生成** | python-docx 生成 Word 文档（产品介绍、报告、表格等） |
| **互联网搜索** | Tavily API 实时搜索新闻、网页、财经信息 |
| **天气查询** | Open-Meteo API（免费，无需 key）获取天气实况和预报 |
| **生成文件管理** | Web 页面浏览/搜索/下载/删除 Agent 生成的 .docx 文件 |
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
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac

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

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
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

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查 |
| POST | `/api/chat/` | 发送聊天消息 |
| POST | `/api/documents/upload` | 上传文档（multipart），返回 task_id 异步处理 |
| GET | `/api/documents/tasks/{task_id}` | 查询上传任务进度（progress + stage） |
| GET | `/api/documents/` | 文档列表 |
| DELETE | `/api/documents/{id}` | 删除文档 |
| GET | `/api/skills/` | 技能列表 |
| POST | `/api/skills/{name}/toggle` | 启用/禁用技能 |
| GET | `/api/plugins/` | 插件列表 |
| POST | `/api/plugins/{name}/toggle` | 启用/禁用插件 |
| GET | `/api/vectors/?offset=0&limit=50&query=xxx&document_id=xxx` | 查看/搜索向量库内容（分页+全文检索） |
| GET | `/api/generated/` | 列出 Agent 生成的 .docx 文件（可选 `?q=关键字` 搜索文件名） |
| GET | `/api/generated/download/{filename}` | 下载生成的 .docx 文件 |
| DELETE | `/api/generated/{filename}` | 删除生成的 .docx 文件 |
| GET | `/api/monitor/stats` | 系统监控统计（请求量/模型调用/token 用量/耗时） |

---

## 聊天界面控制

| 控制项 | 说明 |
|--------|------|
| **Vector DB 开关** | 控制是否启用向量库检索。开启后 Agent 会检索上传的文档内容辅助回答；关闭后仅凭 LLM 自身知识回答，适合闲聊或通用问题。 |
| **模型选择** | 下拉切换 DeepSeek / OpenAI 等 LLM 模型。 |

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

```ini
# .env 可选配置
SUMMARIZATION_MODEL=ollama/qwen2.5:3b     # 摘要用模型（推荐免费方案），不设置则只用截断
SUMMARIZATION_KEEP_MESSAGES=20            # 摘要时保留的最近完整消息数
CHUNK_PAIRS=10                            # 每批摘要的消息对数量（用户+助手为一对）
```

**推荐免费摘要模型：**
| 方案 | 模型 | 说明 |
|------|------|------|
| **本地（推荐）** | `ollama/qwen2.5:3b` | ~1.7GB，中文摘要够用，完全免费，无需 API key |
| 本地 | `ollama/qwen2.5:7b` | ~4.2GB，中文摘要质量更好，但摘要用小模型即可 |
| 免费 API | `gemini/gemini-2.0-flash-lite` | 1500 次/天免费，需配 `GEMINI_API_KEY` |
| 免费 API | `groq/llama3-8b-8192` | 30 req/min，完全免费，需 `GROQ_API_KEY` |

## 生成文件管理

Agent 创建的 .docx 文件（通过 docx-generator 或 kb-export 插件）自动保存到 `backend/data/generated/`，可在前端 **Generated** 页面管理：

- **列表查看**：按创建时间倒序排列
- **搜索**：按文件名关键字过滤
- **下载**：点击 Download 按钮下载原始 .docx 文件
- **删除**：点击 Delete 按钮从磁盘删除

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

已预装以下来自 [Anthropic Agent Skills](https://github.com/anthropics/skills) 的技能包，直接输入需求即可自动触发：

**📝 文档处理与创作**

| Skill | 触发示例 |
|-------|----------|
| **Word 文档** (`docx`) | *"帮我创建一个 Word 文档，内容是产品介绍"* · *"把这个内容导出为 .docx 文件"* · *"帮我写一份报告，格式要好看"* |
| **PPT 演示文稿** (`pptx`) | *"给我做一份 6 页的 PPT，主题是新能源"* · *"把这份大纲变成幻灯片"* · *"帮我美化这个 .pptx 文件"* |
| **PDF 处理** (`pdf`) | *"把这份文档转成 PDF"* · *"提取这个 PDF 中的表格"* · *"合并这几个 PDF 文件"* · *"给 PDF 添加水印"* |
| **Excel 表格** (`xlsx`) | *"创建一个 Excel 表格，包含销售数据"* · *"帮我把这份 CSV 转成 .xlsx"* · *"在这个表格里加个图表"* |

**🎨 设计与视觉创作**

| Skill | 触发示例 |
|-------|----------|
| **前端界面设计** (`frontend-design`) | *"帮我设计一个产品展示的 Landing Page"* · *"做一个仪表盘风格的页面"* · *"美化这个 React 组件"* |
| **算法艺术** (`algorithmic-art`) | *"用 p5.js 画一个粒子系统动画"* · *"生成一张算法艺术图，流场风格"* · *"创建一个创意编程作品"* |
| **海报/画布设计** (`canvas-design`) | *"帮我设计一张海报，主题是科技论坛"* · *"创建一张艺术画布，输出 PNG"* · *"做一个活动宣传图"* |
| **品牌风格** (`brand-guidelines`) | *"应用 Anthropic 的品牌风格到这个页面"* · *"使用品牌的配色方案"* · *"按品牌规范调整这个设计"* |
| **主题定制** (`theme-factory`) | *"给这份 PPT 应用海洋主题"* · *"帮我生成一个自定义主题，暖色调"* · *"应用 sunset-boulevard 主题"* |

**🔧 开发与工具**

| Skill | 触发示例 |
|-------|----------|
| **Claude API 开发** (`claude-api`) | *"帮我写一个调用 Claude API 的代码"* · *"给这段代码加上 prompt caching"* · *"从 Claude 3.5 迁移到 Claude 4"* |
| **文档协作编写** (`doc-coauthoring`) | *"帮我写一份技术方案文档"* · *"一起协作写一篇提案"* · *"帮我起草一份设计文档"* |
| **MCP 服务器构建** (`mcp-builder`) | *"创建一个 MCP 服务器，对接 GitHub API"* · *"用 FastMCP 写一个天气查询工具"* · *"帮我构建一个 MCP server"* |
| **Web 应用测试** (`webapp-testing`) | *"帮我测试本地运行的 Web 应用"* · *"用 Playwright 跑一下这个页面的 E2E 测试"* · *"截图看看这个页面长什么样"* |
| **Web Artifacts 构建** (`web-artifacts-builder`) | *"创建一个多组件交互的 HTML Artifact"* · *"用 React + Tailwind 搭建一个复杂的仪表盘"* · *"使用 shadcn/ui 构建这个页面"* |
| **Slack GIF 制作** (`slack-gif-creator`) | *"帮我做一个欢迎新同事的 GIF，用于 Slack"* · *"创建一个产品发布的动画 GIF"* · *"做一个搞笑的 GIF"* |
| **Skill 创建器** (`skill-creator`) | *"帮我创建一个自定义技能"* · *"优化这个技能的触发描述"* · *"测试这个技能的效果"* |

### Plugins（插件）

预装插件可通过输入需求自动触发：

| Plugin | 工具函数 | 触发示例 |
|--------|----------|----------|
| **example-plugin** | `tool_calculate(expression)` — 计算数学表达式 | *"计算 3.14 * 25 的结果"* · *"算一下 1024 / 8"* |
| | `tool_get_current_time(format)` — 获取当前时间 | *"现在几点了？"* · *"获取当前日期和时间"* |
| | `tool_hello(name)` — 返回问候语 | *"跟张三打个招呼"* |
| **docx-generator** | `tool_create_docx(title, sections)` — 创建 Word 文档 | *"帮我创建一个 Word 文档，内容是产品介绍"* · *"把这段内容导出为 .docx"* |
| **internet-search** | `tool_internet_search(query, max_results, topic)` — 搜索互联网 | *"今天有什么新闻？"* · *"搜索一下 Python 的最新动态"* |
| **weather** | `tool_get_weather(city, forecast_days)` — 查询天气 | *"今天北京天气怎么样？"* · *"伦敦未来三天的天气预报"* |

在聊天框中直接输入需求，Agent 会自动判断需要调用哪些工具来完成你的请求。

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
