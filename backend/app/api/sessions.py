"""会话 REST（兼容 `frontend/src/api/sessions.ts`）。

返回**裸** JSON（非 {code,message,data} 信封），与前端 `frontend/src/api/sessions.ts` 契约一致（旧的 `app/session/router.py` 已删除）：
- `GET  /api/sessions` → `list[SessionInfo]`（支持 kind/search/archived/limit 查询）
- `POST /api/sessions` → `SessionInfo`
- `GET/PATCH /api/sessions/{id}` → `SessionInfo`
- `DELETE /api/sessions/{id}` → 204
- `GET  /api/sessions/{id}/messages` → `list[SessionMessage]`
- `POST /api/sessions/{id}/prompts` / `GET` / `DELETE /{pid}` → 待处理任务队列
- `POST /api/sessions/{id}/interrupt|revert|fork|compact`、`GET /status|children|context`
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..deps import PlatformContext, get_context

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _uid(x_user_id: str | None) -> str:
    return x_user_id or "anonymous"


class SessionCreate(BaseModel):
    title: str = ""
    directory: str | None = None
    kind: str = "multi-agent"
    model: str | None = None


class SessionUpdate(BaseModel):
    title: str | None = None
    archived: int | None = None
    agent: str | None = None
    model: dict | None = None


class PromptEnqueue(BaseModel):
    prompt: dict[str, Any]
    delivery: str = "queue"
    client_msg_id: str | None = None


def _must_get(ctx: PlatformContext, session_id: str):
    sess = ctx.sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return sess


@router.post("")
async def create_session(
    body: SessionCreate,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict:
    sess = ctx.sessions.create(
        title=body.title, directory=body.directory, user_id=_uid(x_user_id),
        kind=body.kind, model=body.model,
    )
    return sess.to_session_info()


@router.get("")
async def list_sessions(
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
    kind: str | None = None,
    search: str | None = None,
    archived: bool = False,
    limit: int | None = None,
) -> list:
    items = ctx.sessions.list(user_id=_uid(x_user_id), kind=kind)
    if search:
        needle = search.lower()
        items = [s for s in items if needle in (s.title or "").lower()]
    if limit:
        items = items[:limit]
    return [s.to_session_info() for s in items]


@router.get("/{session_id}")
async def get_session(session_id: str, ctx: PlatformContext = Depends(get_context)) -> dict:
    return _must_get(ctx, session_id).to_session_info()


@router.patch("/{session_id}")
async def update_session(
    session_id: str, body: SessionUpdate, ctx: PlatformContext = Depends(get_context)
) -> dict:
    _must_get(ctx, session_id)
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.model is not None:
        fields["model"] = body.model.get("id") if isinstance(body.model, dict) else body.model
    sess = ctx.sessions.update(session_id, **fields)
    assert sess is not None
    return sess.to_session_info()


@router.delete("/{session_id}")
async def delete_session(session_id: str, ctx: PlatformContext = Depends(get_context)) -> Response:
    ctx.sessions.delete(session_id)
    return Response(status_code=204)


@router.get("/{session_id}/messages")
async def get_messages(
    session_id: str, ctx: PlatformContext = Depends(get_context)
) -> list:
    _must_get(ctx, session_id)
    return await ctx.session_messages(session_id)


@router.delete("/{session_id}/messages/{message_id}")
async def delete_message(
    session_id: str, message_id: str, ctx: PlatformContext = Depends(get_context)
) -> Response:
    _must_get(ctx, session_id)
    await ctx.hide_message(session_id, message_id)
    return Response(status_code=204)


@router.get("/{session_id}/status")
async def get_status(session_id: str, ctx: PlatformContext = Depends(get_context)) -> dict:
    sess = _must_get(ctx, session_id)
    return {"session_id": session_id, "status": sess.status}


@router.get("/{session_id}/children")
async def get_children(session_id: str, ctx: PlatformContext = Depends(get_context)) -> list:
    _must_get(ctx, session_id)
    return [s.to_session_info() for s in ctx.session_children(session_id)]


@router.get("/{session_id}/state")
async def get_state(session_id: str, ctx: PlatformContext = Depends(get_context)) -> dict:
    _must_get(ctx, session_id)
    return await ctx.state(session_id)


@router.get("/{session_id}/context")
async def get_context_epoch(session_id: str, ctx: PlatformContext = Depends(get_context)) -> dict:
    _must_get(ctx, session_id)
    visible = await ctx.session_messages(session_id)
    return {"session_id": session_id, "message_count": len(visible)}


@router.post("/{session_id}/interrupt")
async def interrupt_session(session_id: str, ctx: PlatformContext = Depends(get_context)) -> Response:
    sess = _must_get(ctx, session_id)
    ctx.cancel_turn(session_id)
    sess.prompts = []
    ctx.sessions.update(session_id, status="idle")
    return Response(status_code=204)


@router.post("/{session_id}/compact")
async def compact_session(
    session_id: str,
    checkpoint: str | None = None,
    ctx: PlatformContext = Depends(get_context),
) -> Response:
    _must_get(ctx, session_id)
    await ctx.compact_session(session_id)
    return Response(status_code=204)


@router.post("/{session_id}/fork")
async def fork_session(
    session_id: str,
    message_id: str | None = None,
    ctx: PlatformContext = Depends(get_context),
) -> dict:
    _must_get(ctx, session_id)
    forked = await ctx.fork_session(session_id, message_id)
    return forked.to_session_info()


@router.post("/{session_id}/revert")
async def revert_session(
    session_id: str, body: dict | None = None, ctx: PlatformContext = Depends(get_context)
) -> dict:
    _must_get(ctx, session_id)
    message_id = str((body or {}).get("message_id") or "")
    return await ctx.revert_session(session_id, message_id)


@router.post("/{session_id}/prompts")
async def enqueue_prompt(
    session_id: str, body: PromptEnqueue, ctx: PlatformContext = Depends(get_context)
) -> dict:
    sess = _must_get(ctx, session_id)
    pid = body.client_msg_id or f"pr_{int(time.time() * 1000)}"
    msg = body.prompt.get("message") if isinstance(body.prompt, dict) else None
    if not any(p["id"] == pid for p in sess.prompts):
        sess.prompts.append({"id": pid, "message": msg or "", "enqueued_at": int(time.time() * 1000)})
    return {"id": pid, "message": msg or "", "enqueued_at": int(time.time() * 1000)}


@router.get("/{session_id}/prompts")
async def list_prompts(session_id: str, ctx: PlatformContext = Depends(get_context)) -> list:
    sess = _must_get(ctx, session_id)
    return list(reversed(sess.prompts))


@router.delete("/{session_id}/prompts/{prompt_id}")
async def remove_prompt(
    session_id: str, prompt_id: str, ctx: PlatformContext = Depends(get_context)
) -> dict:
    sess = _must_get(ctx, session_id)
    sess.prompts = [p for p in sess.prompts if p["id"] != prompt_id]
    return {"removed": True, "id": prompt_id}
