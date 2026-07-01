# Knowledge Base System — RAG + AI Agent

基于 RAG（检索增强生成）的知识库 AI 问答系统，支持文档上传、向量检索、多模型对话、Skills 和 Plugins 动态扩展。

---

## 功能

| 功能 | 说明 |
|------|------|
| **智能问答** | 上传文档后，通过 RAG 检索相关内容，结合 LLM 生成精准回答 |
| **多模型支持** | 前端下拉菜单切换 DeepSeek V3 / R1、OpenAI GPT-4o / 4o-mini |
| **文档管理** | 支持 TXT / MD / PDF 上传，自动分块、向量化存储到 ChromaDB |
| **多轮对话** | 自动生成 conversation_id，支持同一会话内的上下文连续对话 |
| **Skills（技能）** | Markdown 文件定义技能，动态加载，可在 Web 界面启用/禁用 |
| **Plugins（插件）** | Python 文件定义 tool_* 函数（如计算器、查时间），Agent 按需调用 |
| **本地 Embedding** | 使用 sentence-transformers 本地运行，通过 ModelScope 下载模型 |
| **检索重排序** | Cross-encoder 对检索结果重打分（top-3），显著提升回答精度 |
| **上下文管理** | 滑动窗口截断历史（4000 tokens），防止 context 溢出 |
| **对话持久化** | SQLite 存储对话历史，服务重启不丢失 |
| **来源引用** | 回答时标注检索到的文档来源及相似度分数 |

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
python -m venv .venv 或 windows 系统 盘符:\Python313\python.exe -m venv .venv
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

### 启动后端

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload