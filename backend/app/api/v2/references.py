"""Named references API (opencode gap G-07)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.deps import default_session_directory, get_context
from references import (
    delete_reference,
    load_registry,
    write_reference,
)

router = APIRouter(prefix="/v2", tags=["References"])


def _workspace(ctx, session_id: str) -> str | None:
    if not session_id:
        return None
    sess = ctx.sessions.get(session_id)
    directory = getattr(sess, "directory", None) if sess is not None else None
    return directory or default_session_directory(session_id)


def _scope(payload: dict, workspace: str | None) -> str:
    scope = str(payload.get("scope") or ("project" if workspace else "global")).lower()
    if scope not in ("global", "project"):
        raise HTTPException(status_code=400, detail=f"invalid scope: {scope}")
    if scope == "project" and not workspace:
        raise HTTPException(status_code=400, detail="project scope requires session_id")
    return scope


def _validate(payload: dict) -> None:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="reference name is required")
    path = str(payload.get("path") or "").strip()
    repository = str(payload.get("repository") or "").strip()
    if not path and not repository:
        raise HTTPException(status_code=400, detail="path or repository is required")
    if path and repository:
        raise HTTPException(status_code=400, detail="path and repository are mutually exclusive")


@router.get("/references")
async def list_references(
    request: Request, session_id: str = "", include_hidden: bool = False
) -> dict:
    ctx = get_context(request)
    registry = load_registry(_workspace(ctx, session_id))
    return {"references": registry.list(include_hidden=include_hidden)}


@router.post("/references")
async def create_reference(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    payload = await request.json()
    _validate(payload)
    workspace = _workspace(ctx, session_id)
    scope = _scope(payload, workspace)
    name = str(payload["name"]).strip()
    write_reference(name, payload, workspace=workspace, scope=scope)
    registry = load_registry(workspace)
    state = registry.ensure(name)
    return {"reference": state.as_dict(), "scope": scope}


@router.delete("/references/{name}")
async def remove_reference(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    workspace = _workspace(ctx, session_id)
    chosen = _scope({"scope": scope}, workspace)
    if not delete_reference(name, workspace=workspace, scope=chosen):
        raise HTTPException(status_code=404, detail=f"reference not found in {chosen}: {name}")
    return {"deleted": name, "scope": chosen}


@router.post("/references/{name}/refresh")
async def refresh_reference(request: Request, name: str, session_id: str = "") -> dict:
    ctx = get_context(request)
    registry = load_registry(_workspace(ctx, session_id))
    try:
        state = await registry.refresh(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown reference: {name}") from exc
    return {"reference": state.as_dict()}


@router.get("/references/{name}/tree")
async def reference_tree(request: Request, name: str, session_id: str = "") -> dict:
    ctx = get_context(request)
    registry = load_registry(_workspace(ctx, session_id))
    state = registry.ensure(name)
    try:
        entries = registry.tree(name)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"reference {name} is not ready ({state.status})",
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown reference: {name}") from exc
    return {"name": name, "status": state.status, "entries": entries}


__all__ = ["router"]
