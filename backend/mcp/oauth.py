"""OAuth 2.1 authorization for HTTP MCP servers.

The flow follows the MCP authorization specification:

* Protected Resource Metadata discovery (RFC 9728)
* Authorization Server Metadata discovery (RFC 8414 / OIDC)
* PKCE S256 (required)
* RFC 8707 ``resource`` parameter
* Dynamic Client Registration (RFC 7591) when no ``client_id`` is configured
* Access-token refresh and secure-ish local token storage (0600 where supported)

Only discovery and token files are handled here; MCP JSON-RPC stays in
``mcp.client``. Tokens are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .config import MCPServerConfig

logger = logging.getLogger(__name__)

OAUTH_TIMEOUT = 20.0
PENDING_TTL = 600.0
REFRESH_SKEW = 60.0

_CHALLENGE = re.compile(
    r'([A-Za-z0-9_]+)\s*=\s*(?:"([^"]*)"|([^,\s]+))'
)


class OAuthError(RuntimeError):
    """OAuth discovery, registration, exchange or refresh failed."""


@dataclass
class PendingOAuth:
    server: str
    workspace: str | None
    config: MCPServerConfig
    state: str
    code_verifier: str
    redirect_uri: str
    token_endpoint: str
    client_id: str
    client_secret: str
    auth_method: str
    resource: str
    scope: str
    issuer: str
    created_at: float = field(default_factory=time.time)


_PENDING: dict[str, PendingOAuth] = {}
_PENDING_LOCK = threading.Lock()
_STORE_LOCK = threading.RLock()
_REFRESH_LOCKS: dict[Any, asyncio.Lock] = {}
_REFRESH_LOCKS_LOCK = threading.Lock()


def _store_path() -> Path:
    from storage.paths import global_paths

    return global_paths()["state"] / "mcp-oauth.json"


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"tokens": {}, "clients": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"tokens": {}, "clients": {}}
    if not isinstance(raw, dict):
        return {"tokens": {}, "clients": {}}
    if not isinstance(raw.get("tokens"), dict):
        raw["tokens"] = {}
    if not isinstance(raw.get("clients"), dict):
        raw["clients"] = {}
    return raw


def _write_store(store: dict[str, Any]) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(
            json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise OAuthError(f"cannot persist MCP OAuth state: {exc}") from exc


def _key(*parts: str) -> str:
    raw = "\0".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _token_key(config: MCPServerConfig) -> str:
    return _key("token", config.name, config.url)


def _client_key(config: MCPServerConfig, issuer: str, redirect_uri: str) -> str:
    return _key("client", config.name, config.url, issuer, redirect_uri)


def token_record(config: MCPServerConfig) -> dict[str, Any] | None:
    with _STORE_LOCK:
        record = _read_store()["tokens"].get(_token_key(config))
    return dict(record) if isinstance(record, dict) else None


def save_token(config: MCPServerConfig, record: dict[str, Any]) -> None:
    with _STORE_LOCK:
        store = _read_store()
        store["tokens"][_token_key(config)] = dict(record)
        _write_store(store)


def clear_token(config: MCPServerConfig) -> bool:
    with _STORE_LOCK:
        store = _read_store()
        existed = store["tokens"].pop(_token_key(config), None) is not None
        if existed:
            _write_store(store)
        return existed


def clear_oauth_state(config: MCPServerConfig) -> dict[str, int]:
    """Remove this server's token and cached DCR client registrations."""
    with _STORE_LOCK:
        store = _read_store()
        token_removed = int(
            store["tokens"].pop(_token_key(config), None) is not None
        )
        stale = [
            key
            for key, record in store["clients"].items()
            if isinstance(record, dict)
            and record.get("server") == config.name
            and record.get("url") == config.url
        ]
        for key in stale:
            store["clients"].pop(key, None)
        if token_removed or stale:
            _write_store(store)
        return {"token": token_removed, "clients": len(stale)}


def oauth_status(config: MCPServerConfig) -> dict[str, Any]:
    record = token_record(config) or {}
    return {
        "authorized": bool(
            record.get("access_token") or record.get("refresh_token")
        ),
        "expires_at": int(record.get("expires_at") or 0),
        "scope": str(record.get("scope") or ""),
        "issuer": str(record.get("issuer") or ""),
        "has_refresh_token": bool(record.get("refresh_token")),
    }


def _get_client(
    config: MCPServerConfig, issuer: str, redirect_uri: str
) -> dict[str, Any] | None:
    with _STORE_LOCK:
        record = _read_store()["clients"].get(_client_key(config, issuer, redirect_uri))
    return dict(record) if isinstance(record, dict) else None


def _save_client(
    config: MCPServerConfig, issuer: str, redirect_uri: str, record: dict[str, Any]
) -> None:
    with _STORE_LOCK:
        store = _read_store()
        store["clients"][_client_key(config, issuer, redirect_uri)] = dict(record)
        _write_store(store)


def parse_www_authenticate(value: str) -> dict[str, str]:
    """Parse the ``Bearer resource_metadata="..."`` challenge (best effort)."""
    text = str(value or "").strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    out: dict[str, str] = {}
    for match in _CHALLENGE.finditer(text):
        name = match.group(1).lower()
        out[name] = match.group(2) if match.group(2) is not None else match.group(3)
    return out


def _validate_url(url: str) -> None:
    from utils.ssrf import validate_http_url

    problem = validate_http_url(url)
    if problem:
        raise OAuthError(problem)


def _client_kwargs(timeout: float) -> dict[str, Any]:
    from net.egress import httpx_client_kwargs

    kwargs = httpx_client_kwargs()
    kwargs["timeout"] = timeout
    kwargs["follow_redirects"] = False
    return kwargs


async def _get_json(
    url: str, *, headers: dict[str, str] | None = None, timeout: float = OAUTH_TIMEOUT
) -> dict[str, Any]:
    _validate_url(url)
    try:
        async with httpx.AsyncClient(**_client_kwargs(timeout)) as client:
            response = await client.get(
                url, headers=headers or {"Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise OAuthError(f"OAuth request failed for {url}: {exc}") from exc
    if response.status_code >= 400:
        raise OAuthError(
            f"OAuth endpoint {url} returned {response.status_code}: "
            f"{response.text[:200]}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise OAuthError(f"OAuth endpoint {url} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise OAuthError(f"OAuth endpoint {url} returned a non-object body")
    return data


async def _post_json(
    url: str,
    body: dict[str, Any],
    *,
    form: bool = False,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float = OAUTH_TIMEOUT,
) -> tuple[int, dict[str, Any]]:
    _validate_url(url)
    default_headers = {"Accept": "application/json"}
    if headers:
        default_headers.update(headers)
    try:
        async with httpx.AsyncClient(**_client_kwargs(timeout)) as client:
            if form:
                response = await client.post(
                    url, data=body, headers=default_headers, auth=auth
                )
            else:
                response = await client.post(
                    url, json=body, headers=default_headers, auth=auth
                )
    except httpx.HTTPError as exc:
        raise OAuthError(f"OAuth request failed for {url}: {exc}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return response.status_code, data


def _resource_metadata_urls(url: str) -> list[str]:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OAuthError(f"invalid MCP HTTP URL: {url}")
    path = parsed.path.rstrip("/")
    base = f"{parsed.scheme}://{parsed.netloc}"
    urls: list[str] = []
    if path:
        urls.append(f"{base}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{base}/.well-known/oauth-protected-resource")
    return urls


def _authorization_server_urls(issuer: str) -> list[str]:
    parsed = urlsplit(str(issuer or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OAuthError(f"invalid authorization server issuer: {issuer}")
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    urls: list[str] = []
    if path:
        urls.append(f"{base}/.well-known/oauth-authorization-server{path}")
    urls.append(f"{base}/.well-known/oauth-authorization-server")
    urls.append(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
    if path:
        urls.append(f"{base}/.well-known/openid-configuration{path}")
    return list(dict.fromkeys(urls))


async def _probe_resource_metadata(config: MCPServerConfig) -> dict[str, Any] | None:
    """Send an unauthenticated MCP initialize and follow WWW-Authenticate."""
    payload = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agentsuper", "version": "1"},
        },
    }
    _validate_url(config.url)
    try:
        async with httpx.AsyncClient(**_client_kwargs(OAUTH_TIMEOUT)) as client:
            response = await client.post(
                config.url,
                json=payload,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise OAuthError(f"MCP OAuth probe failed: {exc}") from exc
    if response.status_code == 401:
        challenge = parse_www_authenticate(
            response.headers.get("www-authenticate", "")
        )
        metadata_url = challenge.get("resource_metadata", "")
        if not metadata_url:
            raise OAuthError(
                "MCP server requires OAuth but did not advertise "
                "resource_metadata in WWW-Authenticate"
            )
        return await _get_json(metadata_url)
    if response.status_code >= 400:
        raise OAuthError(
            f"MCP OAuth probe returned {response.status_code}: "
            f"{response.text[:200]}"
        )
    raise OAuthError("MCP server did not advertise OAuth protected resource metadata")


async def discover(config: MCPServerConfig) -> dict[str, Any]:
    """Discover PRM and authorization-server metadata for one MCP server."""
    errors: list[str] = []
    metadata: dict[str, Any] | None = None
    if config.oauth_resource_metadata_url:
        try:
            metadata = await _get_json(config.oauth_resource_metadata_url)
        except OAuthError as exc:
            errors.append(str(exc))
    if metadata is None:
        for url in _resource_metadata_urls(config.url):
            try:
                metadata = await _get_json(url)
                break
            except OAuthError as exc:
                errors.append(str(exc))
    if metadata is None:
        try:
            metadata = await _probe_resource_metadata(config)
        except OAuthError as exc:
            errors.append(str(exc))
    if metadata is None:
        detail = errors[-1] if errors else "no metadata"
        raise OAuthError(f"MCP server did not advertise OAuth metadata: {detail}")

    issuers = metadata.get("authorization_servers")
    if not isinstance(issuers, list) or not issuers:
        raise OAuthError("OAuth protected resource metadata has no authorization_servers")
    resource = str(metadata.get("resource") or config.url)
    prm_scopes = metadata.get("scopes_supported")
    scope = " ".join(config.oauth_scopes)
    if not scope and isinstance(prm_scopes, list):
        scope = " ".join(str(item) for item in prm_scopes if str(item).strip())

    for issuer in issuers:
        issuer_text = str(issuer or "").strip()
        if not issuer_text:
            continue
        try:
            as_metadata = await _discover_authorization_server(issuer_text)
        except OAuthError as exc:
            errors.append(str(exc))
            continue
        methods = as_metadata.get("code_challenge_methods_supported")
        if not isinstance(methods, list) or "S256" not in methods:
            raise OAuthError(
                "authorization server does not advertise PKCE S256 support"
            )
        return {
            "metadata": as_metadata,
            "resource": resource,
            "scope": scope or str(as_metadata.get("scope") or ""),
            "issuer": str(as_metadata.get("issuer") or issuer_text),
        }
    detail = errors[-1] if errors else "no authorization server metadata"
    raise OAuthError(f"cannot discover MCP authorization server: {detail}")


async def _discover_authorization_server(issuer: str) -> dict[str, Any]:
    for url in _authorization_server_urls(issuer):
        try:
            metadata = await _get_json(url)
        except OAuthError:
            continue
        declared = str(metadata.get("issuer") or "").strip()
        if declared and declared.rstrip("/") != issuer.rstrip("/"):
            continue
        if metadata.get("authorization_endpoint") and metadata.get("token_endpoint"):
            return metadata
    raise OAuthError(f"cannot load authorization server metadata for {issuer}")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _append_query(url: str, params: dict[str, str]) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: value for key, value in params.items() if value})
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _client_auth(
    client_id: str, client_secret: str, method: str
) -> tuple[tuple[str, str] | None, dict[str, str]]:
    if not client_secret:
        return None, {}
    if method == "client_secret_post":
        return None, {"client_secret": client_secret}
    return (client_id, client_secret), {}


async def _ensure_client(
    config: MCPServerConfig,
    metadata: dict[str, Any],
    issuer: str,
    redirect_uri: str,
    scope: str,
) -> tuple[str, str, str]:
    if config.oauth_client_id:
        method = config.oauth_token_endpoint_auth_method or (
            "client_secret_basic" if config.oauth_client_secret else "none"
        )
        return config.oauth_client_id, config.oauth_client_secret, method

    cached = _get_client(config, issuer, redirect_uri)
    if cached and cached.get("client_id"):
        return (
            str(cached["client_id"]),
            str(cached.get("client_secret") or ""),
            str(cached.get("token_endpoint_auth_method") or "none"),
        )

    endpoint = str(metadata.get("registration_endpoint") or "").strip()
    if not endpoint:
        raise OAuthError(
            "authorization server does not support dynamic client registration; "
            "configure oauth.client_id / oauth.client_secret"
        )
    payload: dict[str, Any] = {
        "client_name": "AgentSuper",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "native",
    }
    if scope:
        payload["scope"] = scope
    status, data = await _post_json(endpoint, payload)
    if status == 400 and "application_type" in json.dumps(data).lower():
        payload.pop("application_type", None)
        status, data = await _post_json(endpoint, payload)
    if status >= 400 or not data.get("client_id"):
        detail = data.get("error_description") or data.get("error") or data.get("detail")
        raise OAuthError(
            f"dynamic client registration failed ({status})"
            + (f": {detail}" if detail else "")
        )
    method = str(data.get("token_endpoint_auth_method") or "none")
    record = {
        "client_id": str(data["client_id"]),
        "client_secret": str(data.get("client_secret") or ""),
        "token_endpoint_auth_method": method,
        "issuer": issuer,
        "redirect_uri": redirect_uri,
        "server": config.name,
        "url": config.url,
    }
    _save_client(config, issuer, redirect_uri, record)
    return record["client_id"], record["client_secret"], method


def _prune_pending() -> None:
    cutoff = time.time() - PENDING_TTL
    for state, pending in list(_PENDING.items()):
        if pending.created_at < cutoff:
            _PENDING.pop(state, None)


async def start_oauth(
    config: MCPServerConfig,
    redirect_uri: str,
    *,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Build an authorization URL and remember the pending PKCE/state."""
    if config.transport != "http":
        raise OAuthError("OAuth is only supported for HTTP MCP servers")
    redirect = str(redirect_uri or "").strip() or config.oauth_redirect_uri
    if not redirect:
        raise OAuthError("OAuth redirect URI is missing")
    redirect_parts = urlsplit(redirect)
    if redirect_parts.scheme not in ("http", "https") or not redirect_parts.netloc:
        raise OAuthError(f"invalid OAuth redirect URI: {redirect}")
    discovery = await discover(config)
    metadata = discovery["metadata"]
    issuer = discovery["issuer"]
    resource = discovery["resource"]
    scope = discovery["scope"]
    client_id, client_secret, auth_method = await _ensure_client(
        config, metadata, issuer, redirect, scope
    )
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    pending = PendingOAuth(
        server=config.name,
        workspace=workspace,
        config=config,
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect,
        token_endpoint=str(metadata["token_endpoint"]),
        client_id=client_id,
        client_secret=client_secret,
        auth_method=auth_method,
        resource=resource,
        scope=scope,
        issuer=issuer,
    )
    with _PENDING_LOCK:
        _prune_pending()
        _PENDING[state] = pending
    authorization_endpoint = str(metadata.get("authorization_endpoint") or "")
    _validate_url(authorization_endpoint)
    authorization_url = _append_query(
        authorization_endpoint,
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": resource,
            "scope": scope,
        },
    )
    return {
        "authorization_url": authorization_url,
        "state": state,
        "redirect_uri": redirect,
        "issuer": issuer,
        "scope": scope,
    }


def _pending(state: str) -> PendingOAuth | None:
    with _PENDING_LOCK:
        _prune_pending()
        return _PENDING.pop(str(state or ""), None)


def _expires_at(expires_in: Any) -> int:
    try:
        seconds = int(expires_in or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return 0
    return int(time.time() + max(0, seconds - REFRESH_SKEW))


def _record_from_token(
    pending: PendingOAuth, data: dict[str, Any]
) -> dict[str, Any]:
    return {
        "server": pending.server,
        "url": pending.config.url,
        "access_token": str(data.get("access_token") or ""),
        "refresh_token": str(data.get("refresh_token") or ""),
        "token_type": str(data.get("token_type") or "Bearer"),
        "expires_at": _expires_at(data.get("expires_in")),
        "scope": str(data.get("scope") or pending.scope),
        "issuer": pending.issuer,
        "resource": pending.resource,
        "token_endpoint": pending.token_endpoint,
        "client_id": pending.client_id,
        "client_secret": pending.client_secret,
        "token_endpoint_auth_method": pending.auth_method,
    }


async def complete_oauth(
    state: str,
    code: str | None,
    error: str | None = None,
    error_description: str | None = None,
) -> dict[str, Any]:
    pending = _pending(state)
    if pending is None:
        raise OAuthError("invalid or expired OAuth state")
    if error:
        raise OAuthError(error_description or error)
    if not code:
        raise OAuthError("authorization code is missing")
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending.redirect_uri,
        "client_id": pending.client_id,
        "code_verifier": pending.code_verifier,
    }
    if pending.resource:
        body["resource"] = pending.resource
    auth, extra = _client_auth(
        pending.client_id, pending.client_secret, pending.auth_method
    )
    body.update(extra)
    status, data = await _post_json(
        pending.token_endpoint, body, form=True, auth=auth
    )
    if status >= 400 or not data.get("access_token"):
        detail = data.get("error_description") or data.get("error") or data.get("detail")
        raise OAuthError(
            f"OAuth token exchange failed ({status})"
            + (f": {detail}" if detail else "")
        )
    record = _record_from_token(pending, data)
    save_token(pending.config, record)
    return {
        "server": pending.server,
        "workspace": pending.workspace,
        "expires_at": record["expires_at"],
        "scope": record["scope"],
        "issuer": record["issuer"],
    }


def _refresh_lock(config: MCPServerConfig) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), _token_key(config))
    with _REFRESH_LOCKS_LOCK:
        lock = _REFRESH_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _REFRESH_LOCKS[key] = lock
        return lock


async def refresh_access_token(
    config: MCPServerConfig, *, force: bool = False
) -> str:
    """Refresh and persist an access token; return ``""`` when unavailable."""
    record = token_record(config)
    if not record or not record.get("refresh_token"):
        return ""
    async with _refresh_lock(config):
        record = token_record(config)
        if not record or not record.get("refresh_token"):
            return ""
        expires_at = int(record.get("expires_at") or 0)
        if not force and record.get("access_token") and (
            expires_at == 0 or expires_at > time.time() + REFRESH_SKEW
        ):
            return str(record["access_token"])
        body: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": str(record["refresh_token"]),
            "client_id": str(record.get("client_id") or ""),
        }
        if record.get("resource"):
            body["resource"] = str(record["resource"])
        if record.get("scope"):
            body["scope"] = str(record["scope"])
        auth, extra = _client_auth(
            str(record.get("client_id") or ""),
            str(record.get("client_secret") or ""),
            str(record.get("token_endpoint_auth_method") or "none"),
        )
        body.update(extra)
        status, data = await _post_json(
            str(record.get("token_endpoint") or ""), body, form=True, auth=auth
        )
        if status >= 400 or not data.get("access_token"):
            error = str(data.get("error") or "").lower()
            if status in (400, 401) and error in (
                "invalid_grant",
                "invalid_client",
                "unauthorized_client",
            ):
                clear_oauth_state(config)
            detail = data.get("error_description") or data.get("error")
            raise OAuthError(
                f"OAuth token refresh failed ({status})"
                + (f": {detail}" if detail else "")
            )
        record.update(
            {
                "access_token": str(data["access_token"]),
                "token_type": str(data.get("token_type") or record.get("token_type") or "Bearer"),
                "expires_at": _expires_at(data.get("expires_in")),
                "scope": str(data.get("scope") or record.get("scope") or ""),
            }
        )
        if data.get("refresh_token"):
            record["refresh_token"] = str(data["refresh_token"])
        save_token(config, record)
        return str(record["access_token"])


async def access_token(config: MCPServerConfig) -> str:
    """Return a usable access token, refreshing it first when needed."""
    record = token_record(config)
    if not record:
        return ""
    expires_at = int(record.get("expires_at") or 0)
    if record.get("access_token") and (
        expires_at == 0 or expires_at > time.time() + REFRESH_SKEW
    ):
        return str(record["access_token"])
    if record.get("refresh_token"):
        try:
            return await refresh_access_token(config)
        except OAuthError:
            return ""
    return ""


__all__ = [
    "OAUTH_TIMEOUT",
    "OAuthError",
    "PendingOAuth",
    "access_token",
    "clear_oauth_state",
    "clear_token",
    "complete_oauth",
    "discover",
    "oauth_status",
    "parse_www_authenticate",
    "refresh_access_token",
    "save_token",
    "start_oauth",
    "token_record",
]
