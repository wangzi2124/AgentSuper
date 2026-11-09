"""MCP server management API (opencode gap G-04)."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.deps import default_session_directory, get_context
from mcp.client import MCPError
from mcp.config import (
    MCPServerConfig,
    delete_server,
    parse_server,
    read_server,
    write_server,
)
from mcp.oauth import (
    OAuthError,
    clear_oauth_state,
    complete_oauth,
    oauth_status,
    start_oauth,
)
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


def _resolve_server(
    name: str, ctx, session_id: str, scope: str = ""
) -> tuple[MCPServerConfig, str | None, str]:
    workspace = _workspace(ctx, session_id)
    if scope:
        chosen = _scope({"scope": scope}, workspace)
        raw = read_server(name, workspace=workspace, scope=chosen)
        if raw is None:
            raise HTTPException(
                status_code=404, detail=f"MCP server not found in {chosen}: {name}"
            )
        config = parse_server(name, raw)
        if config is None:
            raise HTTPException(status_code=400, detail="invalid MCP server entry")
        return config, workspace, chosen
    if workspace:
        raw = read_server(name, workspace=workspace, scope="project")
        if raw is not None:
            config = parse_server(name, raw)
            if config is not None:
                return config, workspace, "project"
    raw = read_server(name, workspace=workspace, scope="global")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    config = parse_server(name, raw)
    if config is None:
        raise HTTPException(status_code=400, detail="invalid MCP server entry")
    return config, workspace, "global"


def _redirect_uri(request: Request, config: MCPServerConfig) -> str:
    configured = str(config.oauth_redirect_uri or "").strip()
    if configured:
        return configured
    return str(request.base_url).rstrip("/") + "/api/v2/mcp/oauth/callback"


def _callback_page(ok: bool, message: str, *, server: str = "") -> str:
    payload = (
        json.dumps(
            {"type": "mcp-oauth", "ok": ok, "server": server},
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    title = "OAuth 授权成功" if ok else "OAuth 授权失败"
    body = (
        "<p>授权成功，可以关闭此窗口并回到 AgentSuper。</p>"
        if ok
        else f"<p>{html.escape(message)}</p>"
    )
    link = (
        f'<p><a href="/mcp?oauth=success&amp;server={html.escape(quote(server, safe=""))}">'
        "返回 MCP 设置</a></p>"
        if ok
        else '<p><a href="/mcp">返回 MCP 设置</a></p>'
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head><body>"
        f"<h1>{title}</h1>{body}{link}"
        f"<script>try{{if(window.opener)window.opener.postMessage({payload},'*');}}"
        "catch(e){}setTimeout(function(){try{window.close();}catch(e){}},200);</script>"
        "</body></html>"
    )


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


@router.get("/servers/{name}/config")
async def server_config(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    _config, workspace, chosen = _resolve_server(name, ctx, session_id, scope)
    raw = read_server(name, workspace=workspace, scope=chosen) or {}
    return {"config": raw, "scope": chosen}


@router.delete("/servers/{name}")
async def remove_server(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    workspace = _workspace(ctx, session_id)
    chosen = _scope({"scope": scope}, workspace)
    raw = read_server(name, workspace=workspace, scope=chosen)
    config = parse_server(name, raw) if raw is not None else None
    if not delete_server(name, workspace=workspace, scope=chosen):
        raise HTTPException(status_code=404, detail=f"MCP server not found in {chosen}: {name}")
    if config is not None:
        try:
            clear_oauth_state(config)
        except OAuthError:
            pass
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


@router.get("/servers/{name}/prompts")
async def server_prompts(name: str) -> dict:
    try:
        return {"prompts": get_mcp_runtime().prompts(name)}
    except MCPError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{name}/prompts/get")
async def get_server_prompt(request: Request, name: str) -> dict:
    payload = await request.json()
    prompt = str(payload.get("prompt") or payload.get("name") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt name is required")
    arguments = payload.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        raise HTTPException(status_code=400, detail="arguments must be an object")
    try:
        return {"prompt": await get_mcp_runtime().get_prompt(name, prompt, arguments)}
    except MCPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.get("/servers/{name}/auth")
async def server_auth_status(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    config, _workspace_path, _scope_name = _resolve_server(name, ctx, session_id, scope)
    return oauth_status(config)


@router.post("/servers/{name}/auth")
async def server_auth(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    config, workspace, _scope_name = _resolve_server(name, ctx, session_id, scope)
    payload: dict[str, Any] = {}
    try:
        body = await request.json()
        if isinstance(body, dict):
            payload = body
    except Exception:  # noqa: BLE001 - empty/non-JSON bodies are fine
        payload = {}
    redirect_uri = str(payload.get("redirect_uri") or "").strip() or _redirect_uri(
        request, config
    )
    try:
        return await start_oauth(config, redirect_uri, workspace=workspace)
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/servers/{name}/auth")
async def server_auth_clear(
    request: Request, name: str, session_id: str = "", scope: str = ""
) -> dict:
    ctx = get_context(request)
    config, _workspace_path, _scope_name = _resolve_server(name, ctx, session_id, scope)
    state = clear_oauth_state(config)
    cleared = bool(state.get("token") or state.get("clients"))
    try:
        await get_mcp_runtime().disconnect(name)
    except MCPError:
        pass
    return {"cleared": cleared}


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    error_description: str = "",
) -> HTMLResponse:
    try:
        result = await complete_oauth(
            state,
            code or None,
            error=error or None,
            error_description=error_description or None,
        )
    except OAuthError as exc:
        return HTMLResponse(
            _callback_page(False, str(exc)),
            status_code=400,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    return HTMLResponse(
        _callback_page(True, "OK", server=str(result.get("server") or "")),
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
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
