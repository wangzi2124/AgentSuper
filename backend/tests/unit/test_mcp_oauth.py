"""MCP OAuth 2.1 discovery, PKCE, token storage and HTTP bearer wiring."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from execution.tool_registry import ToolRegistry
from mcp import config as config_mod
from mcp import oauth
from mcp import runtime as runtime_mod
from mcp.client import HttpTransport, MCPAuthRequired
from mcp.config import parse_server
from mcp.runtime import MCPRuntime


def _use_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(oauth, "_store_path", lambda: tmp_path / "mcp-oauth.json")


def _http_config():
    return parse_server(
        "demo", {"type": "http", "url": "https://mcp.example.com/mcp"}
    )


def test_parse_oauth_fields():
    cfg = parse_server(
        "demo",
        {
            "type": "http",
            "url": "https://mcp.example.com/mcp",
            "oauth": {
                "client_id": "client",
                "client_secret": "secret",
                "token_endpoint_auth_method": "client_secret_post",
                "scopes": ["read", "write"],
                "redirect_uri": "http://127.0.0.1:8000/callback",
                "resource_metadata_url": "https://mcp.example.com/prm",
            },
        },
    )
    assert cfg.oauth_client_id == "client"
    assert cfg.oauth_client_secret == "secret"
    assert cfg.oauth_token_endpoint_auth_method == "client_secret_post"
    assert cfg.oauth_scopes == ("read", "write")
    assert cfg.oauth_redirect_uri == "http://127.0.0.1:8000/callback"
    assert cfg.oauth_resource_metadata_url == "https://mcp.example.com/prm"


def test_parse_www_authenticate():
    parsed = oauth.parse_www_authenticate(
        'Bearer resource_metadata="https://mcp.example.com/prm", scope="read write"'
    )
    assert parsed["resource_metadata"] == "https://mcp.example.com/prm"
    assert parsed["scope"] == "read write"


def test_start_and_complete_oauth(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    cfg = _http_config()
    calls: list[tuple[str, str, dict]] = []

    async def fake_get(url, **kwargs):
        calls.append(("get", url, {}))
        if url.endswith("/.well-known/oauth-protected-resource/mcp"):
            return {
                "resource": "https://mcp.example.com/mcp",
                "authorization_servers": ["https://auth.example.com"],
                "scopes_supported": ["read"],
            }
        if url.endswith("/.well-known/oauth-authorization-server"):
            return {
                "issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "registration_endpoint": "https://auth.example.com/register",
                "code_challenge_methods_supported": ["S256"],
            }
        raise oauth.OAuthError(f"unexpected GET {url}")

    async def fake_post(url, body, **kwargs):
        calls.append(("post", url, body))
        if url.endswith("/register"):
            return 201, {
                "client_id": "client-1",
                "client_secret": "secret-1",
                "token_endpoint_auth_method": "client_secret_basic",
            }
        if url.endswith("/token"):
            return 200, {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "scope": "read",
                "token_type": "Bearer",
            }
        raise oauth.OAuthError(f"unexpected POST {url}")

    monkeypatch.setattr(oauth, "_get_json", fake_get)
    monkeypatch.setattr(oauth, "_post_json", fake_post)

    result = asyncio.run(
        oauth.start_oauth(
            cfg,
            "http://127.0.0.1:8000/api/v2/mcp/oauth/callback",
            workspace="/tmp/ws",
        )
    )
    assert result["issuer"] == "https://auth.example.com"
    assert result["scope"] == "read"
    assert "client_id=client-1" in result["authorization_url"]
    assert "code_challenge_method=S256" in result["authorization_url"]
    assert "resource=https%3A%2F%2Fmcp.example.com%2Fmcp" in result["authorization_url"]

    dcr = next(call for call in calls if call[0] == "post" and call[1].endswith("/register"))
    assert dcr[2]["redirect_uris"] == [
        "http://127.0.0.1:8000/api/v2/mcp/oauth/callback"
    ]
    assert dcr[2]["token_endpoint_auth_method"] == "none"

    done = asyncio.run(oauth.complete_oauth(result["state"], "code-1"))
    assert done["server"] == "demo"
    assert done["workspace"] == "/tmp/ws"
    record = oauth.token_record(cfg)
    assert record is not None
    assert record["access_token"] == "access-1"
    assert record["refresh_token"] == "refresh-1"
    assert oauth.oauth_status(cfg)["authorized"] is True
    assert asyncio.run(oauth.access_token(cfg)) == "access-1"


def test_refresh_token_updates_store(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    cfg = _http_config()
    oauth.save_token(
        cfg,
        {
            "access_token": "expired",
            "refresh_token": "refresh-1",
            "expires_at": int(time.time()) - 10,
            "client_id": "client-1",
            "client_secret": "secret-1",
            "token_endpoint_auth_method": "client_secret_basic",
            "token_endpoint": "https://auth.example.com/token",
            "resource": "https://mcp.example.com/mcp",
        },
    )

    async def fake_post(url, body, **kwargs):
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "refresh-1"
        return 200, {
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        }

    monkeypatch.setattr(oauth, "_post_json", fake_post)
    assert asyncio.run(oauth.refresh_access_token(cfg)) == "access-2"
    record = oauth.token_record(cfg)
    assert record is not None
    assert record["access_token"] == "access-2"
    assert record["refresh_token"] == "refresh-2"


def test_http_transport_adds_bearer_and_retries_after_refresh(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    cfg = _http_config()
    oauth.save_token(
        cfg,
        {
            "access_token": "old",
            "refresh_token": "refresh-1",
            "expires_at": int(time.time()) + 3600,
        },
    )
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        if len(seen) == 1:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": 'Bearer resource_metadata="https://mcp.example.com/prm"'
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        )

    async def fake_refresh(config, **kwargs):
        assert kwargs.get("force") is True
        return "new"

    monkeypatch.setattr(oauth, "refresh_access_token", fake_refresh)

    async def run():
        transport = HttpTransport(cfg)
        transport._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await transport.request("tools/list", {})
        finally:
            await transport.close()

    assert asyncio.run(run()) == {"ok": True}
    assert seen == ["Bearer old", "Bearer new"]


def test_http_transport_raises_auth_required_without_token(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    cfg = _http_config()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={
                "www-authenticate": 'Bearer resource_metadata="https://mcp.example.com/prm"'
            },
        )

    async def run():
        transport = HttpTransport(cfg)
        transport._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await transport.request("initialize", {})
        finally:
            await transport.close()

    with pytest.raises(MCPAuthRequired) as exc:
        asyncio.run(run())
    assert exc.value.resource_metadata == "https://mcp.example.com/prm"


def test_clear_oauth_state_removes_token_and_clients(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    cfg = _http_config()
    oauth.save_token(cfg, {"access_token": "a", "refresh_token": "r"})
    oauth._save_client(
        cfg,
        "https://auth.example.com",
        "http://127.0.0.1:8000/api/v2/mcp/oauth/callback",
        {
            "client_id": "client-1",
            "server": cfg.name,
            "url": cfg.url,
        },
    )
    state = oauth.clear_oauth_state(cfg)
    assert state == {"token": 1, "clients": 1}
    assert oauth.token_record(cfg) is None
    assert (
        oauth._get_client(
            cfg,
            "https://auth.example.com",
            "http://127.0.0.1:8000/api/v2/mcp/oauth/callback",
        )
        is None
    )


def test_runtime_marks_auth_required(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "type": "http",
                        "url": "https://mcp.example.com/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        config_mod, "config_paths", lambda workspace: [("global", path)]
    )

    class AuthConnection:
        def __init__(self, config):
            self.config = config
            self.tools = []
            self.prompts = []

        async def connect(self):
            raise MCPAuthRequired(
                "OAuth authorization required",
                resource_metadata="https://mcp.example.com/prm",
            )

        async def close(self):
            return None

    monkeypatch.setattr(runtime_mod, "MCPConnection", AuthConnection)
    servers = asyncio.run(MCPRuntime().reload(None, ToolRegistry()))
    assert servers[0]["status"] == "auth_required"
    assert servers[0]["auth_required"] is True
    assert servers[0]["oauth"] is True
    assert servers[0]["authorized"] is False


def test_token_store_is_atomic_and_clearable(tmp_path, monkeypatch):
    _use_store(tmp_path, monkeypatch)
    cfg = _http_config()
    oauth.save_token(cfg, {"access_token": "a", "refresh_token": "r"})
    path = tmp_path / "mcp-oauth.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["tokens"]
    assert oauth.clear_token(cfg) is True
    assert oauth.token_record(cfg) is None
    assert oauth.clear_token(cfg) is False
