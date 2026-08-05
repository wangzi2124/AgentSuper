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
- **Reranker model** downloads **ModelScope-first** (HF fallback) via `app/utils/model_download.py:download_model`; in network-restricted envs set `ENABLE_RERANKER=false` in `.env` or pre-download via ModelScope:
  ```powershell
  modelscope download --model cross-encoder/ms-marco-MiniLM-L6-v2 --local_dir data/models/cross-encoder/ms-marco-MiniLM-L6-v2
  ```
  **Runtime degradation**: if `Reranker()` construction or `rerank()` prediction fails, the pipeline degrades (no rerank, order preserved) instead of crashing startup or the query — retriever sub-chains also degrade independently (vector failure → BM25-only)
- **Embedding model** (default: `BAAI/bge-small-zh-v1.5`) locally cached; English fallback `all-MiniLM-L6-v2`. Download via ModelScope:
  ```powershell
  modelscope download --model AI-ModelScope/bge-small-zh-v1.5 --local_dir data/models/BAAI/bge-small-zh-v1.5
  ```
- **ChatRequest** includes `use_vector_db: bool = True`; frontend toggle controls this per-message. Also accepts `files: list[FileContent]` for multimodal, but frontend does not yet send files.
- **Chat SSE streaming**: frontend calls `POST /api/chat/stream`; backend emits SSE events: `step_start`, `step_end`, `tool_start`, `tool_end`, `done`, `error`. SSE error events include `retryable`, `status_code`, `error_type` fields for frontend retry logic. Non-streaming fallback at `POST /api/chat/`.
- **Supervisor routing whitelist** (`app/agent/supervisor.py`): `ROUTABLE_AGENTS = {"rag", "web_search", "code"}`. Decompose validation + LLM prompt only expose routable agents; `handle_message` re-filters subtasks and falls back to `rag`. Prevents LLM returning `"supervisor"` (registered on the bus) causing self-recursive routing → `Agent 'supervisor' did not respond in time`. **Sub-agent timeouts** are env-configurable: `SUB_AGENT_TIMEOUT` (default `150s`, supervisor→sub-agent wait in `_route_to`/`_execute_parallel`), `SUB_AGENT_TIMEOUT_EXTENDED` (default `300s`, applied to tool-intensive agents in `EXTENDED_TIMEOUT_AGENTS`, default `code`) and `SUPERVISOR_TIMEOUT` (default `300s`, endpoint→supervisor wait in `/multi-agent` and `/multi-agent/stream`). Rag generation latency is 5–60s+ (DeepSeek variance + tool loops), so 60s was too tight — `Agent '<agent>' did not respond in time` means that sub-agent exceeded the wait. **Graded timeout**: `AgentBus.send_and_wait` supports grace extensions — if the sub-agent is still active (event loop heartbeat within `grace_window`) at deadline, the wait is extended once per `grace_extensions`. **Error delivery**: sub-agent errors are delivered to the waiting caller as `AgentMessage(type="error")` (payload carries `error`/`error_type`/`completed_steps`), NOT raised as bare exceptions — supervisor `_route_to`/`_execute_parallel` transparently forward the payload.
- **Multi-agent realtime events** (see `docs/multi-agent-realtime-events-design.md`): `/multi-agent/stream` streams per-sub-agent events `agent_start`/`agent_step`/`agent_done`/`agent_error` (frontend keys agents by `agent_id`, steps upserted by `step_id`). Event bridge = request-level `AgentEventCollector` (`app/agent/stream_events.py`) passed to sub-agents via `AgentMessage.payload["_event_queue"]` (supervisor forwards payload unchanged). `RAGAgentWrapper` wraps it as `TaggedEventQueue` handed to `RAGAgent.invoke(event_queue=...)` so `graph.py:_push_event` outputs become `agent_step` (tool_output/tool_heartbeat dropped; permission_request passed through but multi-agent UI has no approval panel → waits `PERMISSION_APPROVAL_TIMEOUT` then denies). Final `done` event + persisted assistant message carry `agents` snapshot (built by `collector.agents_snapshot()`) so history replay shows agent panels.
- **Execution loop guards** (`app/agent/graph.py` `_generate`): `MAX_STEPS` (default `40`, primary cap aligned with opencode `agent.steps`; effective cap = `min(MAX_TOOL_ROUNDS, MAX_STEPS)`, `MAX_TOOL_ROUNDS` default `24` is a hard backstop) — on the last allowed round a `MAX_STEPS_PROMPT` is injected **as an assistant-role message** (aligned with opencode `prompt.ts:1281`) and tools are disabled for the final call, forcing a structured "已完成/未完成/下一步" summary. **Doom-loop detection**: `DOOM_LOOP_THRESHOLD` (default `3`) — identical `tool+args` fingerprint repeated N consecutive rounds injects a strategy-change prompt; **escalation**: after `DOOM_LOOP_MAX_STRIKES` (default `2`) repeated detections it stops prompting and forces the structured summary with tools disabled (opencode-equivalent of `permission.ask(doom_loop)` → deny → stop). Both aligned with opencode `max-steps.ts` / `processor.ts`.
- **Task completion = finish-driven** (`graph.py` `_generate`): reads `response.choices[0].finish_reason` and normalizes via `_normalize_finish_reason` to the opencode `FinishReason` six-value enum (`stop`/`length`/`tool-calls`/`content-filter`/`error`/`unknown`, aligned with `llm/src/schema/ids.ts`). Loop continues while `msg.tool_calls` present or `finish_reason == "tool-calls"` (only `tool-calls` keeps the loop alive, matching `prompt.ts:1113`); `length` appends a truncation notice to the answer; `content-filter` turns the answer into an explicit error surfaced via `steps` events. See `docs/task-completion-design.md`.
- **Long-task / truncation prevention** (aligned with opencode `transform.ts:maxOutputTokens` + file-based output): per-call output cap is `settings.llm_max_tokens` (`LLM_MAX_TOKENS`, default `16384`) — do NOT hardcode in `_llm_call` (`graph.py:447`); `LONG_CONTENT_FILE_RULE` in `backend/app/agent/tools.py` (appended to both the no-KB prompt in `build_system_prompt_no_kb` and the KB prompt via `graph.py:_system_prompt_with_kb`) forces the model to write content > ~500 chars to files (`tool_write_file`/`tool_append_file`/generator plugins) and keep replies to a summary, so single-round output rarely hits `length`. `length` is intentionally NOT auto-continued (matches opencode). See `docs/long-task-design.md`.
- **Model name auto-prefixing** (`graph.py:216-220`): if `model` has no `/`, prepends `deepseek/` or `openai/` based on `api_base` — always use full names like `deepseek/deepseek-v4-flash`
- **AgentState.use_vector_db** (`graph.py:78`): when `false`, `_retrieve` returns empty context immediately — no KB search
- **Tool call JSON error handling** (`graph.py:244-246`): `json.loads(tc.function.arguments)` wrapped in try/except — malformed args return error to LLM instead of crashing
- **Skills**: Markdown files in `backend/skills/*.md` or `backend/skills/<name>/SKILL.md` with YAML frontmatter. Both formats mixed.
- **Plugins**: Python files in `backend/plugins/*.py`, functions named `tool_*` are auto-registered
  - Enabled state: presence of `backend/plugins/<module>.enabled` file (not YAML frontmatter)
  - Disabled by default: `example_plugin.py`, `file_reader.py` (no `.enabled` file)
- **Plugin tool name** format: `plugin_<PLUGIN_NAME>_<func_name>` where `PLUGIN_NAME` matches the `PLUGIN_NAME` attribute in the `.py` file (may contain hyphens, e.g. `plugin_internet-search_tool_internet_search`)
- **filesystem plugin** (`plugin_filesystem_tool_*`): file ops — `tool_ls`, `tool_read_file` (text with offset/limit, multimodal base64 for images/audio/video/pdf), `tool_write_file`, `tool_append_file` (chunked append for large files), `tool_edit_file` (single or global replace), `tool_glob`/`tool_grep` (optional `root` param to search any directory, absolute paths returned for custom roots), `tool_execute` (shell, max 120s timeout). All paths resolved relative to `backend/` workspace. `tool_glob`/`tool_grep` **filter out sensitive files per-match** (workspace `.env`/`*.db`/`permissions.json`, system dirs, un-authorized external paths are silently skipped). **Working directories are configured from the frontend「工作目录」panel** (persisted to `backend/data/runtime_workspaces.json`, runtime-effective, no restart) — the `.env` `EXTRA_WORKSPACES` variable is removed. Outside all workspaces: `EXTERNAL_PATH_DEFAULT` (ask/allow/deny, default `ask`); permission approval waits at most `PERMISSION_APPROVAL_TIMEOUT`s (default 60) then denies. Tool params (`offset`/`limit`/`overwrite`/`replace_all`/`context`/`count_only`/`files_only`/`timeout`) are declared as typed schema AND coerced at runtime, so string-typed numbers from the LLM no longer crash.
- **Admin endpoints & CORS**: sensitive write endpoints (`permission respond`/`workspaces`, `plugins toggle/call`, `skills toggle`, `config/summarization` POST) require `require_admin(request)` (`app/api/deps.py`). With `ADMIN_TOKEN` set → `Authorization: Bearer <token>` required; **unset → only localhost (127.0.0.1/::1) allowed, remote LAN access gets 403**. CORS defaults to localhost vite dev/preview origins (config `cors_origins`, env `CORS_ORIGINS` as JSON array).
- **Generated file plugins** (auto-save to `backend/data/generated/`):
  - `docx_generator.py` → `tool_create_docx(title, sections, output_path)`. Extension auto-forced to `.docx`
  - `pdf_generator.py` → `tool_create_pdf(title, sections, output_path)`. Extension auto-forced to `.pdf`. Requires `reportlab`
  - `excel_generator.py` → `tool_create_excel(sheets, output_path)`. Extension auto-forced to `.xlsx`. Supports multiple sheets. Requires `openpyxl`
  - `kb_export.py` → `tool_export_kb_to_docx(query, title, top_k)`. Combines KB retrieval + docx generation
- **System prompt** (`backend/app/agent/tools.py`): lists available tools + instructions. When adding a new plugin/generator, update this file so the LLM knows about it. Currently has dedicated sections for docx/pdf/xlsx but NOT for pptx. Includes **planning & final report guidance**: multi-step/multi-file tasks must first output a `## 实施计划` checklist, and must always end with a `## 完成情况` section (已完成/未完成/下一步).
- **Conversation persistence**: primary store is `backend/data/session.db` (see "Session management" below); legacy `conversations.db` kept read-only, lazily migrated on access
- **Session management** (`backend/app/session/`):
  - **`session.db`** normalized tables: `sessions` / `session_messages` (append-only event log with per-session `seq`) / `message_parts` / `session_context_epoch` / `session_inputs` (`delivery: steer|queue`) / `session_tasks`. Legacy `conversations.db` stays read-only; lazy migration keeps `conversation_id == session.id`
  - **`/api/sessions` routes** (`router.py`): `POST ''` create, `GET ''` list (`project`/`roots`/`search`/`archived`), `GET/PATCH/DELETE /{id}`, `POST /{id}/fork?message_id=`, `POST /{id}/prompt?delivery=`, `GET /{id}/messages?after_seq=`, `GET /{id}/context`, `POST /{id}/compact`, `POST /{id}/revert`, `POST /{id}/interrupt`, `GET /{id}/children`, `GET /{id}/status`. Isolation via `X-User-Id` (default `anonymous`) + `resolve_session_context` (403 on cross-user access)
  - **SessionService + SessionCoordinator** (`service.py`/`coordinator.py`): per-session serial execution, global `MAX_CONCURRENT_AGENTS` semaphore caps concurrency. Real executor injected by `agent_executor.py:build_executor(app)` (promote input → `history.load` → summarize/truncate/sanitize → `agent.invoke` → persist + `done` SSE event). Writes are serialized per-session via `service.write_lock(session_id)` (held by `agent_executor` persist path and `_persist_multi_agent` in `chat.py`); `fork`/`compact`/`revert` are async and acquire it too. `repository.append_message`/`admit_input` compute `seq` atomically inside `BEGIN IMMEDIATE` (INSERT `SELECT COALESCE(MAX(seq),0)+1`) — no separate read-then-write race
  - **Context epoch + compaction baseline** (`history.py`): `history.load` filters by `max(epoch.baseline_seq, latest compaction seq)`; when compression actually changes history, executor appends a `type='compaction'` message + `replace_epoch_after_compaction` + sets `session.time_compacted`, and `load` always brings back the newest checkpoint as a `system` message → watermark survives restart/replay
  - **Sub-sessions / tasks** (`task_bridge.py` + `bus.py:cancel_pending`): multi-agent requests register a `kind='task'` child session mapped to a bus thread_id; `service.remove`/`interrupt` cascade to child sessions (coordinator + task_bridge); `fork` copies messages (with parts) via `_copy_message` up to `message_id`
  - **Undo/revert** (`repository.revert_to_message`): deletes all messages after the target message (and their parts), rolls epoch baseline back to the newest remaining compaction (0 if none); `service.revert` returns `{deleted, messages}`. Revert also **cascades**: cancels the coordinator run + `task_bridge.cancel_children(child_ids)` + deletes child sessions + `clear_inputs` (drops queued prompts)
  - **Message model**: `Message.type` ∈ user/assistant/system/tool/compaction/epoch; `agent_executor._message_to_history` maps `compaction` → `system` role
- **SummarizationMiddleware** (`backend/app/middleware/summarization.py`): optional **hierarchical** LLM-based context compression. Configure `SUMMARIZATION_MODEL` in `.env` to enable. Falls back to truncation if unset or fails. When it actually compresses, the executor persists a compaction baseline (see Session management above)
- **Chunking**: default `chunk_size=500`/`chunk_overlap=200` from `.env` (doc says 1000 in README but `.env.example` and `config.py` default to 500). **Chapter-aware**: auto-splits at `第X章`/`Chapter X` boundaries, stores `chapter_title`/`chapter_number` in metadata
- **ChromaDB batch limit**: batching at 5000 per call in `vector_store.py` to avoid `ValueError`
- **Hybrid search**: vector + BM25 fused via RRF (weights 0.7/0.3). BM25 index auto-built on startup and updated on each upload. Requires `rank_bm25` + `jieba`
- **Chapter intent detection** (`backend/app/rag/intent.py`): regex matches `第X章`/`Chapter X` → skips vector search, queries `ChapterStore` directly
- **Toggling skills/plugins** at runtime calls `agent.refresh_tools()` which rebuilds LangGraph
- **Monitoring** (`backend/app/monitor.py`): in-memory stats (`record_request`/`record_model_call`), exposed at `GET /api/monitor/stats`. Resets on restart. `RequestLogMiddleware` logs every HTTP request. `_llm_call` wrapper records model, tokens, duration, and tool-rounds.
- **Concurrency control** (`backend/app/api/chat.py`): `asyncio.Semaphore(2)` limits concurrent Agent tasks. When all slots are full, new requests queue and receive a `queued` SSE event with `queue_position`. Frontend displays queue status in sidebar and ChatView header. `GET /api/chat/stream/status` returns current active/queue depth.
- **Session content preservation** (`frontend/src/stores/chat.ts`): `loadConversation()` always fetches from server and merges with IndexedDB cache (`frontend/src/api/session-cache.ts`). SSE streaming messages are persisted to IndexedDB on send/done/error events. Messages survive page refresh and SSE disconnection. `SessionState` tracks `streamPhase` (`idle`/`queued`/`running`) and `queuePosition` per session.
- **Error retry mechanism**: Three-layer retry — litellm `num_retries=2` → TaskRunner exponential backoff (2s/4s/8s, max 3 attempts) → frontend auto-retry countdown (5s, max 2 times) + manual retry button. Errors classified as retryable (429/5xx/network/timeout) vs non-retryable (401/context_overflow). `ChatError` type carries `retryable`/`statusCode` for UI decisions.
- **No test framework or test commands** configured.

### Frontend
- **No lint/typecheck npm script** — only `build` runs vue-tsc then vite build
- **fetch-based API clients** in `frontend/src/api/` (no axios)
- **TypeScript types** in `frontend/src/types/index.ts` shared across stores and views
- **Model list** hardcoded in `frontend/src/stores/chat.ts` (`SUPPORTED_MODELS` constant)
- **Vector DB toggle** in `frontend/src/views/ChatView.vue`: checkbox bound to `chat.useVectorDb`, sent as `use_vector_db` in ChatRequest
- **Generated Files view**: shows all files in `data/generated/`. Has "Run" button for `.js` files (browser sandbox with mock `fs`/`require`). Not for `.docx`/`.pdf`/`.xlsx`.
- **Session cache** (`frontend/src/api/session-cache.ts`): IndexedDB layer for persisting chat messages. `saveSessionToCache()`/`loadSessionFromCache()`/`mergeServerAndCache()` handle dual persistence with server SQLite. Messages saved on send/done/error events; loaded and merged on `loadConversation()`.

### When adding new API routes
- Add backend router in `backend/app/api/`, include via `app.include_router()` in `backend/main.py`
- Add frontend API client in `frontend/src/api/`
- Vite dev proxy forwards `/api/*` to backend — works automatically

### When adding new plugin tool
- Function must be named `tool_<name>`; loader introspects its signature for JSON schema
- Docstring becomes tool description in LLM tool definitions
- Create `backend/plugins/<name>.enabled` file to enable the plugin
- Update `backend/app/agent/tools.py` system prompt if LLM needs explicit instructions about when to use it
