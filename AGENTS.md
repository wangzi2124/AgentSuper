# AGENTS.md — Knowledge Base System (RAG + AI Agent)

## Architecture

- **Two-tier app**: `backend/` (FastAPI + LangGraph + ChromaDB) + `frontend/` (Vue 3 + Pinia + Vite SPA)
- **Backend entrypoint**: `backend/main.py` — FastAPI app, lifespan calls `ensure_runtime_state`
- **Runtime wiring** (`backend/app/runtime.py`): loads `.env` into `os.environ` → VectorStore → LocalEmbeddings → Retriever → Reranker → SkillLoader → PluginLoader → RAGAgent
- **Agent flow** (`backend/app/agent/graph.py`): LangGraph StateGraph — `retrieve` → `rerank` (optional) → `generate`
  - `retrieve` checks `state.use_vector_db`; if `false`, skips retrieval entirely
  - `generate` calls `litellm.acompletion`, supports tool-call loops (max 10 rounds)
- **Config**: `backend/app/config.py` reads `backend/.env` via pydantic-settings with `extra="allow"`
- **Frontend routes**: `/chat` (default), `/documents`, `/skills`, `/plugins`, `/vectors`, `/generated`, `/monitoring`

## Developer Commands

```powershell
# Backend (must use .venv Python directly — activate .venv\Scripts\activate may fail inline)
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd frontend
npm run dev                  # port 5173, proxies /api → localhost:8000

# Build frontend
npm run build                # vue-tsc -b && vite build → dist/
npm run preview

```

## Key Conventions & Gotchas

### Backend
- **Python 3.14+** required (`.python-version`), **use `.venv\Scripts\python.exe`** directly rather than relying on `activate` in subprocesses
- **Config `extra="allow"`** (`backend/app/config.py:10`): `.env` can contain non-Settings variables (e.g. `TAVILY_API_KEY`) without pydantic rejecting them
- **`.env` injected into `os.environ`** at startup by `runtime.py:_load_env_to_os()` so plugins can read env vars via `os.environ.get()`
- **LLM calls** need a real `LLM_API_KEY` in `.env`; LiteLLM cost-map fetch failure is non-blocking (falls back to local backup)
- **Reranker model download fails** from HuggingFace in network-restricted envs → set `ENABLE_RERANKER=false` in `.env`
- **Embedding model** (default: `BAAI/bge-small-zh-v1.5` for Chinese) locally cached at `data/models/BAAI/bge-small-zh-v1.5`; English fallback `all-MiniLM-L6-v2` at `data/models/all-MiniLM-L6-v2`. Download via:
  ```powershell
  modelscope download --model AI-ModelScope/bge-small-zh-v1.5 --local_dir data/models/BAAI/bge-small-zh-v1.5
  ```
- **ChatRequest** includes `use_vector_db: bool = True`; frontend toggle switch controls this per-message. Also accepts `files: list[FileContent]` for multimodal image input, but frontend does not yet send files.
- **Model name auto-prefixing** (`graph.py:187-191`): if `model` has no `/`, it prepends `deepseek/` or `openai/` based on `api_base` — use full names like `deepseek/deepseek-v4-flash` to avoid surprises
- **AgentState.use_vector_db** (`graph.py:31`): when `false`, `_retrieve` returns empty context immediately — no KB search
- **Tool call JSON error handling** (`graph.py:204`): `json.loads(tc.function.arguments)` wrapped in try/except `json.JSONDecodeError` — malformed tool call args return error to LLM instead of crashing
- **Skills**: Markdown files in `backend/skills/*.md` or `backend/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`, `enabled`). Both formats mixed.
- **Plugins**: Python files in `backend/plugins/*.py`, functions named `tool_*` are auto-registered
  - Enabled state: presence of `backend/plugins/<module>.enabled` file (not YAML frontmatter)
  - Disabled by default: `example_plugin.py`, `file_reader.py` (no `.enabled` file)
- **Plugin tool name** format: `plugin_<PLUGIN_NAME>_<func_name>` where `PLUGIN_NAME` matches the `PLUGIN_NAME` attribute in the `.py` file (may contain hyphens, e.g. `plugin_internet-search_tool_internet_search`)
- **filesystem plugin** (`plugin_filesystem_tool_*`): provides 7 file operations — `tool_ls`, `tool_read_file` (text with offset/limit, multimodal base64 for images/audio/video/pdf), `tool_write_file`, `tool_edit_file` (single or global replace), `tool_glob`, `tool_grep` (files-only/count/context modes), `tool_execute` (shell, max 120s timeout). All paths are resolved relative to `backend/` workspace; access outside workspace is denied.
- **Conversation persistence**: SQLite at `backend/data/conversations.db`, sliding window truncation at 4000 tokens
- **SummarizationMiddleware** (`backend/app/middleware/summarization.py`): optional **hierarchical** LLM-based context compression. When `SUMMARIZATION_MODEL` is set in `.env`, old messages are split into chunks (configurable via `CHUNK_PAIRS`, default 10 user/assistant pairs), each chunk summarized independently, and summaries recursively merged until they fit within budget. Recent messages (default 20) kept intact. Falls back to truncation if summarization fails.
- **Chunking**: `chunk_size`/`chunk_overlap` from `.env`, default 1000/200. **Chapter-aware**: auto-splits at `第X章`/`Chapter X` boundaries, stores `chapter_title`/`chapter_number` in metadata
- **ChromaDB batch limit**: batching at 5000 per call in `vector_store.py` to avoid `ValueError("Batch size X exceeds max")`
- **Hybrid search**: vector + BM25 fused via RRF (weights 0.7/0.3). BM25 index auto-built on startup and updated on each upload. Requires `rank_bm25` + `jieba`
- **Chapter intent detection** (`backend/app/rag/intent.py`): regex matches `第X章`/`Chapter X` → skips vector search, queries `ChapterStore` directly
- **Toggling skills/plugins** at runtime calls `agent.refresh_tools()` which rebuilds the LangGraph
- **Generated files** saved to `backend/data/generated/` via `file_generator.save_file()` utility; managed via `GET/DELETE /api/generated/` + `GET /api/generated/download/{filename}`
- **kb-export plugin** (`plugin_kb-export_tool_export_kb_to_docx`): combines KB retrieval + docx generation in one tool call, bypassing LLM content relay. Set via `app.rag.plugin_bridge.set_retriever()` in `runtime.py`
- **Monitoring**: `backend/app/monitor.py` — in-memory stats (`record_request`/`record_model_call`), exposed at `GET /api/monitor/stats`. `RequestLogMiddleware` logs every HTTP request. `_llm_call` wrapper in `graph.py` records model, prompt/completion tokens, duration, and tool-rounds per generation.

### Frontend
- **No lint/typecheck npm script** — only `build` runs vue-tsc then vite build
- **fetch-based API clients** in `frontend/src/api/` (no axios)
- **TypeScript types** in `frontend/src/types/index.ts` shared across stores and views
- **Model list** hardcoded in `frontend/src/stores/chat.ts` (`SUPPORTED_MODELS` constant)
- **Vector DB toggle** in `frontend/src/views/ChatView.vue`: checkbox bound to `chat.useVectorDb`, sent as `use_vector_db` in ChatRequest

### When adding new API routes
- Add backend router in `backend/app/api/`, include in `backend/main.py`
- Add frontend API client in `frontend/src/api/`
- Vite dev proxy forwards `/api/*` to backend — works automatically

### When adding new plugin tool
- Function must be named `tool_<name>`; loader introspects its signature for JSON schema
- Docstring becomes tool description in LLM tool definitions
- Create `backend/plugins/<name>.enabled` file to enable the plugin
