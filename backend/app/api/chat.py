import uuid
import json
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.middleware.summarization import HierarchicalSummarizationMiddleware
from app.models.schemas import ChatRequest, ChatResponse, Source

logger = logging.getLogger(__name__)

router = APIRouter()

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "conversations.db"
MAX_HISTORY_TOKENS = 4000

_summarizer: HierarchicalSummarizationMiddleware | None = None


def _get_summarizer() -> HierarchicalSummarizationMiddleware | None:
    global _summarizer
    if _summarizer is not None:
        return _summarizer
    if not settings.summarization_model:
        return None
    _summarizer = HierarchicalSummarizationMiddleware(
        model=settings.summarization_model,
        trigger=("tokens", MAX_HISTORY_TOKENS),
        keep=("messages", settings.summarization_keep_messages),
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
    )
    return _summarizer


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        "  id TEXT PRIMARY KEY,"
        "  messages TEXT NOT NULL"
        ")"
    )
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


def _save_conversation(conv_id: str, messages: list[dict]):
    conn = _get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO conversations (id, messages) VALUES (?, ?)",
            (conv_id, json.dumps(messages)),
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
            truncated.insert(0, {"role": "system", "content": "[earlier history truncated]"})
            break
        total += tokens
        truncated.insert(0, msg)
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
        raise HTTPException(status_code=500, detail=str(e))

    history.append({"role": "user", "content": body.message})
    history.append({"role": "assistant", "content": result["answer"]})
    _save_conversation(conv_id, history)

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
    )
