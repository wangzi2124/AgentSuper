"""Web Chat v2 HTTP API（Phase 2）。

只接持久投递、会话/消息读取与执行协调；事件流在 Phase 3。
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.contracts.v2 import ModelRef as ContractModelRef
from app.contracts.v2 import PromptInput
from app.deps import PlatformContext, get_context
from app.events.emitter import emit_event
from app.image_processor import enrich_image_files
from app.session import repository as repo

router = APIRouter(prefix="/v2", tags=["v2"])


class SessionCreateV2(BaseModel):
    id: Optional[str] = None
    parent_id: Optional[str] = None
    title: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[ContractModelRef] = None
    directory: Optional[str] = None


class SessionPatchV2(BaseModel):
    title: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    archived: Optional[bool] = None


class RevertStageV2(BaseModel):
    message_id: str
    files: bool = False


class ForkV2(BaseModel):
    before: Optional[str] = None


class DiffV2(BaseModel):
    message_id: str
    file: Optional[str] = None


class RestoreV2(BaseModel):
    message_id: str


class ModelSwitchV2(BaseModel):
    model: ContractModelRef


class AgentSwitchV2(BaseModel):
    agent: str


class DeliveryPatchV2(BaseModel):
    delivery: Literal["steer", "queue"]


class PermissionReplyV2(BaseModel):
    decision: Literal["once", "always", "reject"]
    message: Optional[str] = None


class FormReplyV2(BaseModel):
    response: dict[str, Any] = Field(default_factory=dict)


def _user_id(x_user_id: str | None) -> str:
    return x_user_id or "anonymous"


def _must_get(ctx: PlatformContext, session_id: str):
    sess = ctx.sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session not found")
    return sess


def _model_ref(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    if isinstance(value, dict):
        return {
            "id": str(value.get("id") or ""),
            "provider_id": str(value.get("provider_id") or value.get("providerID") or ""),
            "variant": value.get("variant"),
        }
    return {
        "id": str(getattr(value, "id", "") or ""),
        "provider_id": str(
            getattr(value, "provider_id", None) or getattr(value, "providerID", None) or ""
        ),
        "variant": getattr(value, "variant", None),
    }


def _status_v2(value: str | None) -> str:
    if value == "running":
        return "busy"
    if value == "retry":
        return "retry"
    return "idle"


def _session_v2(ctx: PlatformContext, session_id: str) -> dict[str, Any]:
    info = repo.get_session(session_id)
    mem = ctx.sessions.get(session_id)
    if info is None and mem is None:
        raise HTTPException(status_code=404, detail="session not found")
    if info is not None:
        created = int(info.time_created or 0)
        updated = int(info.time_updated or 0)
        tokens = {
            "input": int(info.tokens_input or 0),
            "output": int(info.tokens_output or 0),
            "reasoning": int(info.tokens_reasoning or 0),
            "cache_read": int(info.tokens_cache_read or 0),
            "cache_write": int(info.tokens_cache_write or 0),
        }
        status = _status_v2(info.status)
        outcome = info.outcome or ("interrupted" if info.status == "interrupted" else None)
        return {
            "id": info.id,
            "parent_id": info.parent_id,
            "project_id": info.project_id,
            "directory": info.directory,
            "title": info.title,
            "agent": info.agent,
            "model": _model_ref(info.model),
            "status": status,
            "outcome": outcome,
            "cost": float(info.cost or 0),
            "tokens": tokens,
            "time": {
                "created": created,
                "updated": updated,
                "busy": info.time_busy,
                "idle": info.time_idle,
                "viewed": info.time_viewed,
                "archived": info.time_archived,
            },
            "metadata": info.metadata or {},
        }
    tok = mem.tokens or {}
    return {
        "id": mem.id,
        "parent_id": mem.parent_id,
        "project_id": "",
        "directory": mem.directory or "",
        "title": mem.title,
        "agent": None,
        "model": _model_ref(mem.model),
        "status": _status_v2(mem.status),
        "outcome": None,
        "cost": float(mem.cost or 0),
        "tokens": {
            "input": int(tok.get("input", 0)),
            "output": int(tok.get("output", 0)),
            "reasoning": int(tok.get("reasoning", 0)),
            "cache_read": int(tok.get("cache_read", 0)),
            "cache_write": int(tok.get("cache_write", 0)),
        },
        "time": {
            "created": int(mem.created_at * 1000),
            "updated": int(mem.updated_at * 1000),
            "busy": None,
            "idle": None,
            "viewed": None,
            "archived": None,
        },
        "metadata": {},
    }


def _inbox_item_v2(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("prompt") or {}
    if item.get("type") == "compaction":
        inner = {
            "type": "compaction",
            "payload": {"reason": payload.get("reason", "manual")},
            "delivery": item.get("delivery", "steer"),
        }
    else:
        inner = {
            "type": "user",
            "payload": {
                "text": payload.get("message") or payload.get("text") or "",
                "files": payload.get("files") or [],
                "agents": payload.get("agents") or [],
                "skills": payload.get("skills") or [],
                "metadata": payload.get("metadata") or {},
            },
            "delivery": item.get("delivery", "steer"),
        }
    return {
        "id": item["id"],
        "session_id": item["session_id"],
        "item": inner,
        "status": item["status"],
        "admitted_seq": item["admitted_seq"],
        "promoted_seq": item.get("promoted_seq"),
        "time_created": item["time_created"],
        "time_delivered": item.get("time_delivered"),
    }


def _part_v2(part: Any) -> dict[str, Any] | None:
    data = part.data or {}
    if part.type == "text":
        return {"type": "text", "text": str(data.get("text") or data.get("content") or "")}
    if part.type == "reasoning":
        return {"type": "reasoning", "text": str(data.get("text") or data.get("content") or "")}
    if part.type != "tool":
        return None
    raw_state = str(data.get("state") or "completed")
    args = data.get("args")
    if not isinstance(args, dict):
        try:
            import json

            parsed = json.loads(data.get("arguments") or "{}")
            args = parsed if isinstance(parsed, dict) else {}
        except Exception:
            args = {}
    tool_id = str(data.get("tool_call_id") or part.id)
    if raw_state in ("pending", "running", "streaming"):
        state = {"status": "running", "input": args}
    elif raw_state == "error":
        state = {
            "status": "error",
            "input": args,
            "error": {
                "type": "tool_error",
                "message": str(data.get("error") or "tool failed"),
            },
            "content": ([{"type": "text", "text": str(data.get("output") or "")}]
                        if data.get("output") else []),
        }
    else:
        state = {
            "status": "completed",
            "input": args,
            "content": ([{"type": "text", "text": str(data.get("output") or "")}]
                        if data.get("output") else []),
            "metadata": {},
        }
    return {
        "type": "tool",
        "id": tool_id,
        "name": str(data.get("name") or "tool"),
        "state": state,
        "time_created": int(part.time_created or 0),
    }


def _message_v2(raw: Any) -> dict[str, Any] | None:
    data = raw.data or {}
    if raw.type == "user":
        return {
            "type": "user",
            "id": raw.id,
            "time_created": int(raw.time_created or 0),
            "text": str(data.get("content") or ""),
            "files": data.get("files") or [],
            "agents": [],
            "skills": [],
            "metadata": {},
            "voice": data.get("voice"),
        }
    if raw.type == "assistant":
        content = []
        for p in repo.list_parts(raw.id):
            mapped = _part_v2(p)
            if mapped is not None:
                content.append(mapped)
        text = str(data.get("content") or "")
        if text and not any(p.get("type") == "text" for p in content):
            content.append({"type": "text", "text": text})
        return {
            "type": "assistant",
            "id": raw.id,
            "time_created": int(raw.time_created or 0),
            "time_completed": None,
            "agent": None,
            "model": None,
            "content": content,
            "finish": None,
            "cost": None,
            "tokens": None,
            "error": None,
        }
    if raw.type == "system":
        return {
            "type": "system",
            "id": raw.id,
            "time_created": int(raw.time_created or 0),
            "text": str(data.get("content") or ""),
        }
    if raw.type == "compaction":
        return {
            "type": "compaction",
            "id": raw.id,
            "time_created": int(raw.time_created or 0),
            "status": "completed",
            "reason": str(data.get("reason") or "auto"),
            "summary": str(data.get("summary") or ""),
            "model": _model_ref(data.get("model")),
            "cost": data.get("cost"),
            "tokens": data.get("tokens"),
            "error": None,
        }
    return None


def _messages_v2(session_id: str, *, limit: int, order: str, type_filter: str | None) -> list[dict[str, Any]]:
    sess = repo.get_session(session_id)
    hidden = set(sess.hidden_ids if sess is not None else [])
    rows = repo.list_messages(session_id)
    out: list[dict[str, Any]] = []
    for raw in rows:
        if raw.id in hidden:
            continue
        if type_filter and raw.type != type_filter:
            continue
        mapped = _message_v2(raw)
        if mapped is not None:
            out.append(mapped)
    if order == "desc":
        out.reverse()
    return out[: max(1, int(limit))]


@router.post("/session")
async def create_session(
    body: SessionCreateV2,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        sess = ctx.sessions.create(
            title=body.title or "",
            user_id=_user_id(x_user_id),
            session_id=body.id,
            model=body.model.id if body.model else None,
            parent_id=body.parent_id,
            directory=body.directory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.agent:
        repo.update_session(sess.id, agent=body.agent)
    payload = _session_v2(ctx, sess.id)
    await emit_event(
        ctx, "session.created", {"session": payload}, session_id=sess.id
    )
    return {"data": payload}


@router.get("/session")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=500),
    order: Literal["asc", "desc"] = "desc",
    search: str | None = None,
    archived: bool = False,
    parent_id: str | None = Query(default=None, alias="parentID"),
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    infos = repo.list_sessions(
        _user_id(x_user_id),
        search=search,
        archived=archived,
        limit=limit,
    )
    if parent_id:
        infos = [i for i in infos if i.parent_id == parent_id]
    ctx.sessions.hydrate_rows(infos)
    if order == "asc":
        infos = list(reversed(infos))
    return {"data": [_session_v2(ctx, info.id) for info in infos]}


@router.get("/session/{session_id}")
async def get_session(
    session_id: str, ctx: PlatformContext = Depends(get_context)
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    return {"data": _session_v2(ctx, session_id)}


@router.patch("/session/{session_id}")
async def update_session(
    session_id: str,
    body: SessionPatchV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.archived is not None:
        fields["archived"] = body.archived
    if body.metadata is not None:
        fields["metadata"] = body.metadata
    if fields:
        repo.update_session(session_id, **fields)
        if "title" in fields:
            ctx.sessions.update(session_id, title=body.title)
        if body.archived is True:
            await emit_event(
                ctx, "session.archived", {"session_id": session_id}, session_id=session_id
            )
        elif body.archived is False:
            await emit_event(
                ctx, "session.unarchived", {"session_id": session_id}, session_id=session_id
            )
        if body.title is not None:
            await emit_event(
                ctx,
                "session.renamed",
                {"session_id": session_id, "title": body.title},
                session_id=session_id,
            )
        if body.metadata is not None:
            await emit_event(
                ctx,
                "session.updated",
                {"session_id": session_id},
                session_id=session_id,
            )
    return {"data": _session_v2(ctx, session_id)}


@router.delete("/session/{session_id}", status_code=204)
async def delete_session(
    session_id: str, ctx: PlatformContext = Depends(get_context)
) -> None:
    _must_get(ctx, session_id)
    await emit_event(
        ctx, "session.deleted", {"session_id": session_id}, session_id=session_id
    )
    ctx.sessions.delete(session_id)


@router.post("/session/{session_id}/prompt")
async def prompt(
    session_id: str,
    body: PromptInput,
    ctx: PlatformContext = Depends(get_context),
    x_user_id: str | None = Header(default=None),
) -> dict[str, Any]:
    sess = _must_get(ctx, session_id)
    if sess.user_id not in ("anonymous", _user_id(x_user_id)):
        raise HTTPException(status_code=403, detail="forbidden")
    message_id = body.id or f"msg_{uuid.uuid4().hex[:12]}"
    legacy_files = [
        {
            "filename": f.name or f.uri.rsplit("/", 1)[-1] or "附件",
            "data": f.data or "",
            "mime_type": f.mime or "",
        }
        for f in body.files
        if f.data or f.uri
    ]
    files = await enrich_image_files(legacy_files) if legacy_files else []
    payload = {
        "type": "user",
        "message": body.text,
        "text": body.text,
        "files": files,
        "agents": [a.model_dump() for a in body.agents],
        "skills": [s.model_dump() for s in body.skills],
        "metadata": body.metadata,
        "voice": body.voice.model_dump() if body.voice else None,
    }
    existed = repo.get_input(session_id, message_id) is not None
    try:
        repo.admit_input(
            session_id, payload, delivery=body.delivery, input_id=message_id
        )
    except sqlite3.IntegrityError:
        if repo.get_input(session_id, message_id) is None:
            raise
    item = repo.get_input(session_id, message_id)
    if item is None:
        raise HTTPException(status_code=500, detail="prompt admission failed")
    if not existed:
        await emit_event(
            ctx,
            "session.inbox.enqueued",
            {
                "session_id": session_id,
                "inbox_id": item["id"],
                "item": _inbox_item_v2(item)["item"],
            },
            session_id=session_id,
        )
    if body.resume is not False:
        await ctx.v2_coordinator.wake(session_id)
    return {"data": _inbox_item_v2(item)}


@router.get("/session/{session_id}/inbox")
async def list_inbox(
    session_id: str,
    include_resolved: bool = False,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    return {
        "data": [
            _inbox_item_v2(item)
            for item in repo.list_inbox(session_id, include_resolved=include_resolved)
        ]
    }


@router.patch("/session/{session_id}/inbox/{inbox_id}")
async def update_inbox(
    session_id: str,
    inbox_id: str,
    body: DeliveryPatchV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    repo.update_input_delivery(session_id, inbox_id, body.delivery)
    item = repo.get_input(session_id, inbox_id)
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    return {"data": _inbox_item_v2(item)}


@router.delete("/session/{session_id}/inbox/{inbox_id}", status_code=204)
async def cancel_inbox(
    session_id: str,
    inbox_id: str,
    ctx: PlatformContext = Depends(get_context),
) -> None:
    _must_get(ctx, session_id)
    if not repo.cancel_input(session_id, inbox_id):
        raise HTTPException(status_code=404, detail="pending inbox item not found")
    await emit_event(
        ctx,
        "session.inbox.cancelled",
        {"session_id": session_id, "inbox_id": inbox_id},
        session_id=session_id,
    )


@router.post("/session/{session_id}/interrupt")
async def interrupt(
    session_id: str,
    resume: bool = False,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    interrupted = ctx.v2_coordinator.interrupt(session_id)
    return {"data": {"interrupted": bool(interrupted), "resume": resume}}


@router.get("/session/{session_id}/message")
async def list_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    order: Literal["asc", "desc"] = "asc",
    type: str | None = None,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    return {
        "data": _messages_v2(session_id, limit=limit, order=order, type_filter=type),
        "cursor": {"previous": None, "next": None},
    }


@router.post("/session/{session_id}/compact")
async def compact(
    session_id: str,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    input_id = f"cmp_{uuid.uuid4().hex[:12]}"
    repo.admit_input(
        session_id,
        {"type": "compaction", "reason": "manual"},
        delivery="steer",
        input_id=input_id,
    )
    item = repo.get_input(session_id, input_id)
    if item is None:
        raise HTTPException(status_code=500, detail="compaction admission failed")
    await emit_event(
        ctx,
        "session.inbox.enqueued",
        {
            "session_id": session_id,
            "inbox_id": item["id"],
            "item": _inbox_item_v2(item)["item"],
        },
        session_id=session_id,
    )
    await ctx.v2_coordinator.wake(session_id)
    return {"data": _inbox_item_v2(item)}


@router.post("/session/{session_id}/revert/stage")
async def revert_stage(
    session_id: str,
    body: RevertStageV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    if repo.get_message(session_id, body.message_id) is None:
        raise HTTPException(status_code=404, detail="message not found")
    state = {"message_id": body.message_id, "files": bool(body.files)}
    repo.update_session(session_id, revert_state=state)
    await emit_event(
        ctx, "session.revert.staged", {"session_id": session_id, "revert": state},
        session_id=session_id,
    )
    return {"data": state}


@router.post("/session/{session_id}/revert/commit")
async def revert_commit(
    session_id: str,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    info = repo.get_session(session_id)
    state = info.revert_state if info is not None else None
    if not state or not state.get("message_id"):
        raise HTTPException(status_code=409, detail="no staged revert")
    result = await ctx.revert_session(session_id, str(state["message_id"]))
    repo.update_session(session_id, revert_state=None)
    await emit_event(
        ctx, "session.revert.committed", {"session_id": session_id, "revert": state},
        session_id=session_id,
    )
    return {"data": result}


@router.delete("/session/{session_id}/revert", status_code=204)
async def revert_clear(
    session_id: str, ctx: PlatformContext = Depends(get_context)
) -> None:
    _must_get(ctx, session_id)
    repo.update_session(session_id, revert_state=None)
    await emit_event(
        ctx, "session.revert.cleared", {"session_id": session_id}, session_id=session_id
    )


@router.post("/session/{session_id}/diff")
async def turn_diff(
    session_id: str,
    body: DiffV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    from app.snapshot.vfs import render_diff

    descriptor = ctx.get_snapshot(session_id, body.message_id)
    if not descriptor:
        return {"data": {"files": [], "truncated": False, "reason": "no snapshot", "steps": 0}}
    return {"data": render_diff(ctx.vfs, descriptor, file=body.file or None)}


@router.post("/session/{session_id}/restore")
async def restore_turn(
    session_id: str,
    body: RestoreV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    from app.snapshot.vfs import restore

    descriptor = ctx.get_snapshot(session_id, body.message_id)
    if not descriptor:
        return {
            "data": {
                "restored": False,
                "already": False,
                "internal": 0,
                "external": 0,
                "restored_files": [],
                "missing": [],
            }
        }
    return {"data": restore(ctx.vfs, descriptor)}


@router.post("/session/{session_id}/fork")
async def fork(
    session_id: str,
    body: ForkV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    forked = await ctx.fork_session(session_id, body.before)
    payload = _session_v2(ctx, forked.id)
    await emit_event(
        ctx, "session.created", {"session": payload}, session_id=forked.id
    )
    await emit_event(
        ctx,
        "session.forked",
        {
            "session_id": forked.id,
            "parent_id": session_id,
            "boundary": body.before,
        },
        session_id=forked.id,
    )
    return {"data": payload}


@router.post("/session/{session_id}/model")
async def switch_model(
    session_id: str,
    body: ModelSwitchV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    try:
        await ctx.switch_model(session_id, body.model.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    payload = _session_v2(ctx, session_id)
    await emit_event(
        ctx,
        "session.model.selected",
        {"session_id": session_id, "model": payload["model"]},
        session_id=session_id,
    )
    return {"data": payload}


@router.post("/session/{session_id}/agent")
async def switch_agent(
    session_id: str,
    body: AgentSwitchV2,
    ctx: PlatformContext = Depends(get_context),
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    repo.update_session(session_id, agent=body.agent)
    await emit_event(
        ctx,
        "session.agent.selected",
        {"session_id": session_id, "agent": body.agent},
        session_id=session_id,
    )
    return {"data": _session_v2(ctx, session_id)}


@router.get("/session/{session_id}/permission")
async def list_permissions(
    session_id: str, ctx: PlatformContext = Depends(get_context)
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    return {"data": repo.list_permissions(session_id)}


@router.post("/session/{session_id}/permission/{request_id}/reply", status_code=204)
async def reply_permission(
    session_id: str,
    request_id: str,
    body: PermissionReplyV2,
    ctx: PlatformContext = Depends(get_context),
) -> None:
    sess = _must_get(ctx, session_id)
    if not repo.reply_permission(session_id, request_id, body.decision):
        raise HTTPException(status_code=404, detail="permission request not found")
    if body.decision == "always":
        request = next(
            (p for p in repo.list_permissions(session_id, status=None) if p["id"] == request_id),
            None,
        )
        if request is not None:
            for resource in request.get("resources") or []:
                repo.save_permission_rule(sess.user_id or "anonymous", request["action"], resource)
    await emit_event(
        ctx,
        "permission.replied",
        {"session_id": session_id, "request_id": request_id, "reply": body.decision},
        session_id=session_id,
    )


@router.get("/session/{session_id}/form")
async def list_forms(
    session_id: str, ctx: PlatformContext = Depends(get_context)
) -> dict[str, Any]:
    _must_get(ctx, session_id)
    return {"data": repo.list_forms(session_id)}


@router.post("/session/{session_id}/form/{form_id}/reply", status_code=204)
async def reply_form(
    session_id: str,
    form_id: str,
    body: FormReplyV2,
    ctx: PlatformContext = Depends(get_context),
) -> None:
    _must_get(ctx, session_id)
    if not repo.reply_form(session_id, form_id, body.response):
        raise HTTPException(status_code=404, detail="form not found")
    await emit_event(
        ctx,
        "form.replied",
        {"session_id": session_id, "form_id": form_id, "response": body.response},
        session_id=session_id,
    )


@router.get("/event")
async def subscribe_events(
    request: Request,
    types: str | None = None,
    ctx: PlatformContext = Depends(get_context),
) -> StreamingResponse:
    allowed = {item.strip() for item in types.split(",") if item.strip()} if types else None
    sub = ctx.events.subscribe(allowed)

    async def gen():
        import json

        try:
            connected = {
                "id": f"evt_{uuid.uuid4().hex[:16]}",
                "created": int(time.time() * 1000),
                "type": "server.connected",
                "data": {"version": "2"},
                "durable": None,
            }
            yield (
                f"id: {connected['id']}\n"
                "event: server.connected\n"
                f"data: {json.dumps(connected, ensure_ascii=False)}\n\n"
            )
            while not sub.closed:
                if await request.is_disconnected():
                    break
                event = await sub.get(timeout=15)
                if event is None:
                    if sub.closed:
                        break
                    yield ": ping\n\n"
                    continue
                yield (
                    f"id: {event['id']}\n"
                    f"event: {event['type']}\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )
        finally:
            sub.close()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )