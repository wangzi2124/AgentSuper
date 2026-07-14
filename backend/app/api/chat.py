import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.middleware.summarization import HierarchicalSummarizationMiddleware
from app.models.schemas import ChatRequest, ChatResponse, Source, StepEvent

logger = logging.getLogger(__name__)

router = APIRouter()

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "conversations.db"
MAX_HISTORY_TOKENS = 4000

_summarizer: HierarchicalSummarizationMiddleware | None = None
_summarizer_model: str | None = None


def _get_summarizer() -> HierarchicalSummarizationMiddleware | None:
    global _summarizer, _summarizer_model
    current_model = settings.summarization_model

    if current_model != _summarizer_model:
        _summarizer = None
        _summarizer_model = current_model

    if _summarizer is not None:
        return _summarizer
    if not current_model:
        return None
    _summarizer = HierarchicalSummarizationMiddleware(
        model=current_model,
        trigger=("tokens", MAX_HISTORY_TOKENS),
        keep=("messages", settings.summarization_keep_messages),
        api_key=settings.summarization_api_key or settings.llm_api_key,
        api_base=settings.summarization_api_base or settings.llm_api_base,
    )
    return _summarizer


def reset_summarizer():
    global _summarizer, _summarizer_model
    _summarizer = None
    _summarizer_model = None


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "  id TEXT PRIMARY KEY,"
        "  title TEXT NOT NULL DEFAULT '',"
        "  messages TEXT NOT NULL,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    for col in ["title", "created_at", "updated_at"]:
        if col not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
    now = datetime.now().isoformat()
    conn.execute("UPDATE conversations SET created_at = ? WHERE created_at = ''", (now,))
    conn.execute("UPDATE conversations SET updated_at = ? WHERE updated_at = ''", (now,))
    conn.commit()
    return conn


def _load_conversation(conv_id: str) -> list[dict]:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT messages FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return []
    finally:
        conn.close()


def _generate_title(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content", "")
            text = text.strip().replace("\n", " ")
            return text[:20] + ("..." if len(text) > 20 else "")
    return "新对话"


def _save_conversation(conv_id: str, messages: list[dict], title: str | None = None):
    conn = _get_db()
    try:
        now = datetime.now().isoformat()
        if title is not None:
            conn.execute(
                "INSERT INTO conversations (id, title, messages, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET title=excluded.title, messages=excluded.messages, updated_at=excluded.updated_at",
                (conv_id, title, json.dumps(messages), now, now),
            )
        else:
            conn.execute(
                "INSERT INTO conversations (id, messages, created_at, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET messages=excluded.messages, updated_at=excluded.updated_at",
                (conv_id, json.dumps(messages), now, now),
            )
        conn.commit()
    finally:
        conn.close()


def _truncate_history(history: list[dict], max_tokens: int = MAX_HISTORY_TOKENS) -> list[dict]:
    if not history:
        return []
    total = 0
    truncated = []
    for msg in reversed(history):
        tokens = len(msg.get("content", "")) // 2
        if total + tokens > max_tokens:
            truncated.append({"role": "system", "content": "[earlier history truncated]"})
            break
        total += tokens
        truncated.append(msg)
    truncated.reverse()
    return truncated


@router.post("/", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest):
    agent = request.app.state.agent

    conv_id = body.conversation_id or str(uuid.uuid4())

    if body.conversation_id:
        raw = _load_conversation(conv_id)
        if not raw and body.conversation_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = raw
    else:
        history = []

    summarizer = _get_summarizer()
    if summarizer:
        compressed = await summarizer.apply(history)
    else:
        compressed = _truncate_history(history)

    # Build multimodal user content
    user_content: list[dict] = [{"type": "text", "text": body.message}]
    for f in body.files:
        if f.mime_type.startswith("image/"):
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{f.mime_type};base64,{f.data}"},
            })

    try:
        result = await agent.invoke(body.message, model=body.model, history=compressed, use_vector_db=body.use_vector_db, files=[f.model_dump() for f in body.files])
    except Exception as e:
        logger.exception("chat invocation failed")
        raise HTTPException(status_code=500, detail="Internal server error")

    history.append({"id": str(uuid.uuid4()), "role": "user", "content": body.message})
    history.append({"id": str(uuid.uuid4()), "role": "assistant", "content": result["answer"]})
    title = _generate_title(history) if not body.conversation_id else None
    _save_conversation(conv_id, history, title=title)

    return ChatResponse(
        answer=result["answer"],
        sources=[
            Source(
                document_id=s["document_id"],
                content=s["content"],
                score=s["score"],
            )
            for s in result["sources"]
        ],
        conversation_id=conv_id,
        steps=[StepEvent(**s) for s in result.get("steps", [])],
    )


@router.post("/stream")
async def chat_stream(request: Request, body: ChatRequest):
    agent = request.app.state.agent

    conv_id = body.conversation_id or str(uuid.uuid4())

    if body.conversation_id:
        raw = _load_conversation(conv_id)
        if not raw and body.conversation_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = raw
    else:
        history = []

    summarizer = _get_summarizer()
    if summarizer:
        compressed = await summarizer.apply(history)
    else:
        compressed = _truncate_history(history)

    event_queue: asyncio.Queue = asyncio.Queue()

    async def run_agent():
        try:
            result = await agent.invoke(
                body.message, model=body.model, history=compressed,
                use_vector_db=body.use_vector_db,
                files=[f.model_dump() for f in body.files],
                event_queue=event_queue,
            )
            await event_queue.put({
                "type": "done",
                "answer": result["answer"],
                "sources": [
                    {"document_id": s["document_id"], "content": s["content"], "score": s["score"]}
                    for s in result["sources"]
                ],
                "conversation_id": conv_id,
                "title": title,
                "steps": result.get("steps", []),
            })
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("chat stream invocation failed")
            await event_queue.put({"type": "error", "detail": str(e)})

    async def event_generator(user_msg_id: str, assistant_msg_id: str):
        task = asyncio.create_task(run_agent())
        try:
            while True:
                event = await event_queue.get()
                if event["type"] == "done":
                    event["user_msg_id"] = user_msg_id
                    event["assistant_msg_id"] = assistant_msg_id
                    for msg in history:
                        if msg["id"] == assistant_msg_id:
                            msg["content"] = event["answer"]
                            break
                    _save_conversation(conv_id, history)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("done", "error"):
                    break
            await task
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    history.append({"id": user_msg_id, "role": "user", "content": body.message})
    history.append({"id": assistant_msg_id, "role": "assistant", "content": ""})
    title = _generate_title(history) if not body.conversation_id else None
    _save_conversation(conv_id, history, title=title)

    return StreamingResponse(event_generator(user_msg_id, assistant_msg_id), media_type="text/event-stream")


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    conn = _get_db()
    try:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.delete("/conversations/{conversation_id}/messages/{message_id}")
async def delete_message(conversation_id: str, message_id: str):
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT messages FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = json.loads(row[0])
        messages = [m for m in messages if m.get("id") != message_id]
        conn.execute(
            "UPDATE conversations SET messages = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(messages), conversation_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}


@router.get("/conversations")
async def list_conversations():
    conn = _get_db()
    try:
        cursor = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        )
        rows = cursor.fetchall()
        return [
            {"id": r[0], "title": r[1] or "新对话", "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT title, messages, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {
            "id": conversation_id,
            "title": row[0] or "新对话",
            "messages": json.loads(row[1]),
            "created_at": row[2],
            "updated_at": row[3],
        }
    finally:
        conn.close()


@router.put("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: dict):
    title = body.get("title")
    if title is None:
        raise HTTPException(status_code=400, detail="title is required")
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, conversation_id),
        )
        conn.commit()
        if conn.total_changes == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
    finally:
        conn.close()
    return {"status": "ok"}
