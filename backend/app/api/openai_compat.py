"""OpenAI-compatible chat completions facade (opencode gap G-19).

The facade never owns conversation state: it runs a normal platform turn and
maps events to the OpenAI wire format. Session continuity uses the optional
``X-Session-Id`` header.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.deps import get_context

router = APIRouter(prefix="/v1", tags=["OpenAI"])


def _last_user(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if str(message.get("role")) != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
    return ""


def _completion(text: str, model: str, session_id: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "session_id": session_id,
    }


def _delta(text: str, model: str, session_id: str, finish: str | None = None) -> str:
    payload = {
        "id": "chatcmpl-stream",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": text} if text else ({} if finish is None else {}),
                "finish_reason": finish,
            }
        ],
        "session_id": session_id,
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _run_turn(request: Request, body: dict[str, Any]) -> tuple[str, str, str]:
    ctx = get_context(request)
    model = str(body.get("model") or "")
    session_id = request.headers.get("X-Session-Id", "").strip()
    created = False
    if not session_id or ctx.sessions.get(session_id) is None:
        session_id = ctx.create(title="OpenAI API", model=model or None).id
        created = True
    elif model:
        existing = getattr(ctx.sessions.get(session_id), "model", None)
        if existing != model:
            await ctx.switch_model(session_id, model)
    return session_id, model, created


@router.get("/models")
async def list_models() -> dict:
    from models.catalog import get_catalog

    entries = []
    for item in get_catalog():
        mid = str(item.get("id") or "")
        if not mid:
            continue
        entries.append(
            {
                "id": mid,
                "object": "model",
                "created": 0,
                "owned_by": str(item.get("provider") or mid.split("/", 1)[0]),
            }
        )
    return {"object": "list", "data": entries}


@router.get("/models/{model_id:path}")
async def get_model(model_id: str) -> dict:
    from models.catalog import get_catalog

    for item in get_catalog():
        if str(item.get("id") or "") == model_id:
            return {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": str(item.get("provider") or model_id.split("/", 1)[0]),
            }
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail=f"model not found: {model_id}")


@router.post("/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()
    messages = body.get("messages") or []
    question = _last_user(messages)
    if not question.strip():
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="no user message")
    session_id, model, _created = await _run_turn(request, body)
    ctx = get_context(request)

    if not bool(body.get("stream")):
        result = await ctx.run(question, session_id=session_id)
        text = str((result or {}).get("final_output") or (result or {}).get("answer") or "")
        return _completion(text, model, session_id)

    async def generate():
        yield _delta("", model, session_id)
        try:
            async for event in ctx.stream(question, session_id=session_id):
                kind = event.get("type") or event.get("event")
                data = event.get("data") or {}
                if kind == "text_delta":
                    chunk = data.get("delta") or data.get("text") or ""
                    if chunk:
                        yield _delta(str(chunk), model, session_id)
            yield _delta("", model, session_id, finish="stop")
        except Exception as exc:  # noqa: BLE001 - surface as a terminal chunk
            error = {"error": {"message": str(exc) or repr(exc), "type": "server_error"}}
            yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


__all__ = ["router"]
