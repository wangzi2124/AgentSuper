"""MCP OAuth API routes (auth start, callback, clear)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth as auth
from app.api.v2 import mcp as mcp_api


def _client() -> TestClient:
    app = FastAPI()
    app.state.platform = SimpleNamespace(sessions={}, registry=object())
    app.include_router(mcp_api.router, prefix="/api")
    return TestClient(app)


def _server(*_args, **_kwargs):
    return {"type": "http", "url": "https://mcp.example.com/mcp"}


def test_auth_start_returns_url(monkeypatch):
    async def fake_start(config, redirect_uri, *, workspace=None):
        assert config.name == "demo"
        assert redirect_uri.endswith("/api/v2/mcp/oauth/callback")
        return {"authorization_url": "https://auth.example.com/authorize"}

    monkeypatch.setattr(mcp_api, "read_server", _server)
    monkeypatch.setattr(mcp_api, "start_oauth", fake_start)
    response = _client().post("/api/v2/mcp/servers/demo/auth")
    assert response.status_code == 200
    assert response.json()["authorization_url"] == "https://auth.example.com/authorize"


def test_auth_start_rejects_stdio(monkeypatch):
    monkeypatch.setattr(
        mcp_api,
        "read_server",
        lambda *_args, **_kwargs: {"command": "echo"},
    )
    response = _client().post("/api/v2/mcp/servers/demo/auth")
    assert response.status_code == 400
    assert "HTTP" in response.json()["detail"]


def test_oauth_callback_success_and_failure(monkeypatch):
    async def fake_complete(state, code, error=None, error_description=None):
        if state == "bad":
            raise mcp_api.OAuthError("invalid state")
        return {"server": "demo"}

    monkeypatch.setattr(mcp_api, "complete_oauth", fake_complete)
    client = _client()

    ok = client.get("/api/v2/mcp/oauth/callback?state=good&code=code-1")
    assert ok.status_code == 200
    assert "授权成功" in ok.text
    assert "mcp-oauth" in ok.text

    bad = client.get("/api/v2/mcp/oauth/callback?state=bad&code=code-1")
    assert bad.status_code == 400
    assert "invalid state" in bad.text


def test_callback_page_escapes_server_name():
    page = mcp_api._callback_page(
        True, "OK", server="</script><script>alert(1)</script>"
    )
    assert "</script><script>alert(1)</script>" not in page
    assert "\\u003c/script\\u003e" in page


def test_auth_middleware_exempts_oauth_callback(monkeypatch):
    monkeypatch.setattr(auth, "enabled", lambda: True)
    called: list[str] = []

    async def downstream(scope, receive, send):
        called.append(scope["path"])

    middleware = auth.AuthMiddleware(downstream)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v2/mcp/oauth/callback",
                "headers": [],
            },
            None,
            None,
        )
    )
    assert called == ["/api/v2/mcp/oauth/callback"]


def test_auth_clear_disconnects(monkeypatch):
    cleared: list[str] = []
    disconnected: list[str] = []

    monkeypatch.setattr(mcp_api, "read_server", _server)
    monkeypatch.setattr(
        mcp_api,
        "clear_oauth_state",
        lambda config: cleared.append(config.name) or {"token": 1, "clients": 0},
    )

    class Runtime:
        async def disconnect(self, name):
            disconnected.append(name)

    monkeypatch.setattr(mcp_api, "get_mcp_runtime", lambda: Runtime())
    response = _client().delete("/api/v2/mcp/servers/demo/auth")
    assert response.status_code == 200
    assert response.json() == {"cleared": True}
    assert cleared == ["demo"]
    assert disconnected == ["demo"]
