"""MCP server management API (opencode gap G-04)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.deps import default_session_directory, get_context
from mcp.client import MCPError
from mcp.config import delete_server, parse_server, read_server, write_server
from mcp.runtime import get_mcp_runtime

router = APIRouter(prefix="/v2/mcp", tags=["MCP"])


def _workspace(ctx, session_id: str) -> str | None:
    if not session_id:
        return None
    sess = ctx.sessions.get(session_id)
    directory = getattr(sess, "directory", None) if sess is not None else None
    return directory or default_session_directory(session_id)


def _scope(payload: dict[str, Any], workspace: str | None) -> str:
    scope = str(payload.get("scope") or ("project" if workspace else "global")).lower()
    if scope not in ("project", "global"):
        raise HTTPException(status_code=400, detail=f"invalid scope: {scope}")
    if scope == "project" and not workspace:
        raise HTTPException(status_code=400, detail="project scope requires session_id")
    return scope


def _validate(name: str, payload: dict[str, Any]) -> None:
    config = parse_server(name, payload)
    if config is None:
        raise HTTPException(status_code=400, detail="server entry must be an object")
    problem = config.validate()
    if problem:
        raise HTTPException(status_code=400, detail=problem)


async def _reload(ctx, workspace: str | None) -> list[dict[str, Any]]:
    return await get_mcp_runtime().reload(workspace, ctx.registry)


@router.get("")
async def list_servers(request: Request) -> dict:
    return {"servers": get_mcp_runtime().status()}


@router.post("/reload")
async def reload_servers(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    servers = await _reload(ctx, _workspace(ctx, session_id))
    return {"servers": servers}


@router.post("/servers")
async def create_server(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="server name is required")
    workspace = _workspace(ctx, session_id)
    scope = _scope(payload, workspace)
    if read_server(name, workspace=workspace, scope=scope) is not None:
        raise HTTPException(status_code=409, detail=f"server already exists: {name}")
    _validate(name, payload)
    write_server(name, payload, workspace=workspace, scope=scope)
    await _reload(ctx, workspace)
    return {"server": read_server(name, workspace=workspace, scope=scope), "scope": scope}


@router.patch("/servers/{name}")
async def update_server(request: Request, name: str, session_id: str = "") -> dict:
    ctx = get_context(request)
    payload = await request.json()
    workspace = _workspace(ctx, session_id)
    scope = _scope(payload, workspace)
    existing = read_server(name, workspace=workspace, scope=scope)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found in {scope}: {name}")
    merged = dict(existing)
    merged.update(payload)
    merged["name"] = name
    _validate(name, merged)
    write_server(name, merged, workspace=workspace, scope=scope)
    await _reload(ctx, workspace)
    return {"server": read_server(name, workspace=workspace, scope=scope), "scope": scope}


@router.delete("/servers/{name}")
async def remove_server(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    workspace = _workspace(ctx, session_id)
    chosen = _scope({"scope": scope}, workspace)
    if not delete_server(name, workspace=workspace, scope=chosen):
        raise HTTPException(status_code=404, detail=f"MCP server not found in {chosen}: {name}")
    await _reload(ctx, workspace)
    return {"deleted": name, "scope": chosen}


@router.get("/servers/{name}/tools")
async def server_tools(name: str) -> dict:
    try:
        return {"tools": get_mcp_runtime().tools(name)}
    except MCPError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/servers/{name}/resources")
async def server_resources(name: str) -> dict:
    try:
        return {"resources": get_mcp_runtime().resources(name)}
    except MCPError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{name}/resources/read")
async def read_server_resource(request: Request, name: str) -> dict:
    payload = await request.json()
    uri = str(payload.get("uri") or "").strip()
    if not uri:
        raise HTTPException(status_code=400, detail="uri is required")
    try:
        return {"contents": await get_mcp_runtime().read_resource(name, uri)}
    except MCPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/servers/{name}/instructions")
async def server_instructions(name: str) -> dict:
    try:
        return {"instructions": get_mcp_runtime().server_instructions(name)}
    except MCPError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{name}/auth")
async def server_auth(name: str) -> dict:
    raise HTTPException(
        status_code=501,
        detail="OAuth MCP servers are not supported yet; configure headers manually",
    )


@router.post("/{name}/connect")
async def connect_server(request: Request, name: str) -> dict:
    ctx = get_context(request)
    try:
        server = await get_mcp_runtime().connect(name, ctx.registry)
    except MCPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"server": server}


@router.post("/{name}/disconnect")
async def disconnect_server(request: Request, name: str) -> dict:
    try:
        server = await get_mcp_runtime().disconnect(name)
    except MCPError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"server": server}


__all__ = ["router"]
