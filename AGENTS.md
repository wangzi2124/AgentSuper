# AGENTS.md — Knowledge Base System (RAG + AI Agent)

## Architecture

- **Two-tier app**: `backend/` (FastAPI + LangGraph + ChromaDB) + `frontend/` (Vue 3 + Pinia + Vite SPA)
- **Backend entrypoint**: `backend/main.py` — FastAPI app, lifespan calls `ensure_runtime_state`
- **Runtime wiring** (`backend/app/runtime.py`): `_load_env_to_os()` → VectorStore → LocalEmbeddings → Retriever → Reranker → SkillLoader → PluginLoader → RAGAgent
- **Agent flow** (`backend/app/agent/graph.py`): LangGraph StateGraph — `retrieve` → `rerank` (optional) → `generate`
  - `retrieve` checks `state.use_vector_db`; if `false`, skips retrieval entirely
  - `generate` calls `litellm.acompletion`, supports tool-call loops (max 10 rounds)
- **Config**: `backend/app/config.py` reads `backend/.env` via pydantic-settings with `extra="allow"` (non-Settings env vars allowed)
- **Frontend routes**: `/chat` (default), `/documents`, `/skills`, `/plugins`, `/vectors`, `/generated`, `/monitoring`

## Developer Commands

```powershell
# Backend (must use .venv Python directly)
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (port 5173, proxies /api → localhost:8000)
cd frontend
npm run dev

# Build frontend
npm run build          # vue-tsc -b && vite build → dist/
npm run preview
```

## Key Conventions & Gotchas

### Backend
- **Python 3.14+** required (`.python-version`), **use `.venv\Scripts\python.exe`** directly (activate fails in subprocesses)
- **`.env` injected into `os.environ`** at startup by `runtime.py:_load_env_to_os()` so plugins can read env vars via `os.environ.get()`
- **LLM calls** need a real `LLM_API_KEY` in `.env`; LiteLLM cost-map fetch failure is non-blocking (falls back to local backup)
- **Reranker model download fails** from HuggingFace in network-restricted envs → set `ENABLE_RERANKER=false` in `.env`
- **Embedding model** (default: `BAAI/bge-small-zh-v1.5`) locally cached; English fallback `all-MiniLM-L6-v2`. Download via ModelScope:
  ```powershell
  modelscope download --model AI-ModelScope/bge-small-zh-v1.5 --local_dir data/models/BAAI/bge-small-zh-v1.5
  ```
- **ChatRequest** includes `use_vector_db: bool = True`; frontend toggle controls this per-message. Also accepts `files: list[FileContent]` for multimodal, but frontend does not yet send files.
- **Chat SSE streaming**: frontend calls `POST /api/chat/stream`; backend emits SSE events: `step_start`, `step_end`, `tool_start`, `tool_end`, `done`, `error`. Non-streaming fallback at `POST /api/chat/`.
- **Model name auto-prefixing** (`graph.py:216-220`): if `model` has no `/`, prepends `deepseek/` or `openai/` based on `api_base` — always use full names like `deepseek/deepseek-v4-flash`
- **AgentState.use_vector_db** (`graph.py:78`): when `false`, `_retrieve` returns empty context immediately — no KB search
- **Tool call JSON error handling** (`graph.py:244-246`): `json.loads(tc.function.arguments)` wrapped in try/except — malformed args return error to LLM instead of crashing
- **Skills**: Markdown files in `backend/skills/*.md` or `backend/skills/<name>/SKILL.md` with YAML frontmatter. Both formats mixed.
- **Plugins**: Python files in `backend/plugins/*.py`, functions named `tool_*` are auto-registered
  - Enabled state: presence of `backend/plugins/<module>.enabled` file (not YAML frontmatter)
  - Disabled by default: `example_plugin.py`, `file_reader.py` (no `.enabled` file)
- **Plugin tool name** format: `plugin_<PLUGIN_NAME>_<func_name>` where `PLUGIN_NAME` matches the `PLUGIN_NAME` attribute in the `.py` file (may contain hyphens, e.g. `plugin_internet-search_tool_internet_search`)
- **filesystem plugin** (`plugin_filesystem_tool_*`): 7 file ops — `tool_ls`, `tool_read_file` (text with offset/limit, multimodal base64 for images/audio/video/pdf), `tool_write_file`, `tool_edit_file` (single or global replace), `tool_glob`, `tool_grep` (files-only/count/context modes), `tool_execute` (shell, max 120s timeout). All paths resolved relative to `backend/` workspace; access outside workspace denied.
- **Generated file plugins** (auto-save to `backend/data/generated/`):
  - `docx_generator.py` → `tool_create_docx(title, sections, output_path)`. Extension auto-forced to `.docx`
  - `pdf_generator.py` → `tool_create_pdf(title, sections, output_path)`. Extension auto-forced to `.pdf`. Requires `reportlab`
  - `excel_generator.py` → `tool_create_excel(sheets, output_path)`. Extension auto-forced to `.xlsx`. Supports multiple sheets. Requires `openpyxl`
  - `kb_export.py` → `tool_export_kb_to_docx(query, title, top_k)`. Combines KB retrieval + docx generation
- **System prompt** (`backend/app/agent/tools.py`): lists available tools + instructions. When adding a new plugin/generator, update this file so the LLM knows about it. Currently has dedicated sections for docx/pdf/xlsx but NOT for pptx.
- **Conversation persistence**: SQLite at `backend/data/conversations.db`, sliding window truncation at 4000 tokens
- **SummarizationMiddleware** (`backend/app/middleware/summarization.py`): optional **hierarchical** LLM-based context compression. Configure `SUMMARIZATION_MODEL` in `.env` to enable. Falls back to truncation if unset or fails.
- **Chunking**: default `chunk_size=500`/`chunk_overlap=200` from `.env` (doc says 1000 in README but `.env.example` and `config.py` default to 500). **Chapter-aware**: auto-splits at `第X章`/`Chapter X` boundaries, stores `chapter_title`/`chapter_number` in metadata
- **ChromaDB batch limit**: batching at 5000 per call in `vector_store.py` to avoid `ValueError`
- **Hybrid search**: vector + BM25 fused via RRF (weights 0.7/0.3). BM25 index auto-built on startup and updated on each upload. Requires `rank_bm25` + `jieba`
- **Chapter intent detection** (`backend/app/rag/intent.py`): regex matches `第X章`/`Chapter X` → skips vector search, queries `ChapterStore` directly
- **Toggling skills/plugins** at runtime calls `agent.refresh_tools()` which rebuilds LangGraph
- **Monitoring** (`backend/app/monitor.py`): in-memory stats (`record_request`/`record_model_call`), exposed at `GET /api/monitor/stats`. Resets on restart. `RequestLogMiddleware` logs every HTTP request. `_llm_call` wrapper records model, tokens, duration, and tool-rounds.
- **No test framework or test commands** configured.

### Frontend
- **No lint/typecheck npm script** — only `build` runs vue-tsc then vite build
- **fetch-based API clients** in `frontend/src/api/` (no axios)
- **TypeScript types** in `frontend/src/types/index.ts` shared across stores and views
- **Model list** hardcoded in `frontend/src/stores/chat.ts` (`SUPPORTED_MODELS` constant)
- **Vector DB toggle** in `frontend/src/views/ChatView.vue`: checkbox bound to `chat.useVectorDb`, sent as `use_vector_db` in ChatRequest
- **Generated Files view**: shows all files in `data/generated/`. Has "Run" button for `.js` files (browser sandbox with mock `fs`/`require`). Not for `.docx`/`.pdf`/`.xlsx`.

### When adding new API routes
- Add backend router in `backend/app/api/`, include via `app.include_router()` in `backend/main.py`
- Add frontend API client in `frontend/src/api/`
- Vite dev proxy forwards `/api/*` to backend — works automatically

### When adding new plugin tool
- Function must be named `tool_<name>`; loader introspects its signature for JSON schema
- Docstring becomes tool description in LLM tool definitions
- Create `backend/plugins/<name>.enabled` file to enable the plugin
- Update `backend/app/agent/tools.py` system prompt if LLM needs explicit instructions about when to use it
