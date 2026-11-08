"""Agent registry CRUD API (opencode gap G-05). Bodies are never returned."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agents.registry import VALID_MODES, delete_agent, load_registry, save_agent
from app.deps import default_session_directory, get_context

router = APIRouter(prefix="/v2", tags=["Agents"])


def _workspace(ctx, session_id: str) -> str | None:
    if not session_id:
        return None
    sess = ctx.sessions.get(session_id)
    directory = getattr(sess, "directory", None) if sess is not None else None
    return directory or default_session_directory(session_id)


def _serialize(agent) -> dict:
    return {
        "name": agent.name,
        "description": agent.description,
        "mode": agent.mode,
        "tier": agent.tier,
        "model": agent.model,
        "tools": list(agent.tools),
        "permission": dict(agent.permission),
        "request": dict(agent.request),
        "hidden": agent.hidden,
        "disabled": agent.disabled,
        "color": agent.color,
        "steps": agent.max_rounds,
        "source": agent.source,
        "path": agent.path,
    }


def _validate(payload: dict) -> None:
    mode = str(payload.get("mode") or "subagent").strip().lower()
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"invalid mode: {mode}")
    if not str(payload.get("name") or payload.get("id") or "").strip():
        raise HTTPException(status_code=400, detail="agent name is required")


def _scope(payload: dict, workspace: str | None) -> str:
    scope = str(payload.get("scope") or ("project" if workspace else "global")).lower()
    if scope not in ("project", "global"):
        raise HTTPException(status_code=400, detail=f"invalid scope: {scope}")
    if scope == "project" and not workspace:
        raise HTTPException(status_code=400, detail="project scope requires session_id")
    return scope


@router.get("/agents")
async def list_agents(
    request: Request,
    session_id: str = "",
    mode: str = "",
    include_hidden: bool = False,
    include_disabled: bool = False,
) -> dict:
    ctx = get_context(request)
    registry = load_registry(_workspace(ctx, session_id))
    return {
        "agents": [
            _serialize(agent)
            for agent in registry.list(
                include_hidden=include_hidden,
                include_disabled=include_disabled,
                mode=mode or None,
            )
        ]
    }


@router.get("/agents/{name}")
async def get_agent(request: Request, name: str, session_id: str = "") -> dict:
    ctx = get_context(request)
    registry = load_registry(_workspace(ctx, session_id))
    agent = registry.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {name}")
    return {"agent": _serialize(agent)}


@router.post("/agents")
async def create_agent(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    payload = await request.json()
    _validate(payload)
    workspace = _workspace(ctx, session_id)
    scope = _scope(payload, workspace)
    registry = load_registry(workspace)
    existing = registry.get(str(payload.get("name") or payload.get("id")))
    if existing is not None and existing.source == scope:
        raise HTTPException(status_code=409, detail=f"agent already exists: {existing.name}")
    try:
        agent = save_agent(payload, workspace=workspace, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent": _serialize(agent)}


@router.patch("/agents/{name}")
async def update_agent(request: Request, name: str, session_id: str = "") -> dict:
    ctx = get_context(request)
    payload = await request.json()
    workspace = _workspace(ctx, session_id)
    registry = load_registry(workspace)
    existing = registry.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"unknown agent: {name}")
    merged = _serialize(existing)
    merged.update(payload)
    merged["name"] = name
    _validate(merged)
    scope = _scope(payload, workspace)
    try:
        agent = save_agent(merged, workspace=workspace, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"agent": _serialize(agent)}


@router.delete("/agents/{name}")
async def remove_agent(request: Request, name: str, session_id: str = "", scope: str = "") -> dict:
    ctx = get_context(request)
    workspace = _workspace(ctx, session_id)
    chosen = _scope({"scope": scope}, workspace)
    if not delete_agent(name, workspace=workspace, scope=chosen):
        raise HTTPException(status_code=404, detail=f"agent definition not found in {chosen}: {name}")
    return {"deleted": name, "scope": chosen}


__all__ = ["router"]
