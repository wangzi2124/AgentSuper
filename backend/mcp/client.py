"""Real MCP client transports: stdio and streamable HTTP (gap G-04).

Only JSON-RPC is spoken here; tool registration into AgentSuper's registry is
the runtime's job. Failures are explicit (raise) so the UI can show a status
instead of silently pretending a server is connected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from .config import MCPServerConfig

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


class MCPAuthRequired(MCPError):
    """The HTTP MCP server rejected the request and needs OAuth authorization."""

    def __init__(
        self,
        message: str,
        *,
        resource_metadata: str = "",
        scope: str = "",
    ) -> None:
        super().__init__(message)
        self.resource_metadata = resource_metadata
        self.scope = scope


class StdioTransport:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self.config.env)
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, ValueError) as exc:
            raise MCPError(f"cannot start MCP server {self.config.name}: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._process and self._process.stderr
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            logger.debug("[mcp:%s] %s", self.config.name, line.decode(errors="replace").rstrip())

    async def _read_loop(self) -> None:
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8").strip())
            except (ValueError, UnicodeDecodeError):
                logger.warning("[mcp:%s] non-JSON line", self.config.name)
                continue
            message_id = message.get("id")
            if message_id is not None and message_id in self._pending:
                future = self._pending.pop(message_id)
                if not future.done():
                    future.set_result(message)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPError(f"MCP server {self.config.name} closed"))
        self._pending.clear()

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise MCPError("MCP stdio transport is not running")
        async with self._write_lock:
            self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            await self._process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        try:
            message = await asyncio.wait_for(
                future, timeout=self.config.timeout_for(method)
            )
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise MCPError(f"MCP request timed out: {method}") from exc
        if "error" in message:
            raise MCPError(str(message["error"].get("message") or message["error"]))
        return message.get("result")

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        self._process = None


class HttpTransport:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._next_id = 0
        self._session_id = ""
        self.initialize_result: dict[str, Any] = {}

    async def start(self) -> None:
        from net.egress import httpx_client_kwargs

        kwargs = httpx_client_kwargs()
        kwargs["timeout"] = self.config.timeout
        self._client = httpx.AsyncClient(**kwargs)
        try:
            result = await self.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "agentsuper", "version": "1" },
                },
            )
            self.initialize_result = result if isinstance(result, dict) else {}
        except Exception:
            await self.close()
            raise

    def _headers(self, token: str = "") -> dict[str, str]:
        headers = dict(self.config.headers)
        headers.setdefault("Accept", "application/json, text/event-stream")
        if self._session_id:
            headers.setdefault("Mcp-Session-Id", self._session_id)
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")
        return headers

    async def _access_token(self) -> str:
        from . import oauth as oauth_mod

        try:
            return await oauth_mod.access_token(self.config)
        except oauth_mod.OAuthError as exc:
            raise MCPAuthRequired(str(exc)) from exc

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            raise MCPError("MCP http transport is not running")
        self._next_id += 1
        request_id = self._next_id
        token = await self._access_token()
        refresh_error = ""
        response: httpx.Response | None = None
        attempts = 2 if token else 1
        for attempt in range(attempts):
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
            try:
                response = await self._client.post(
                    self.config.url,
                    json=payload,
                    headers=self._headers(token),
                    timeout=self.config.timeout_for(method),
                )
            except httpx.HTTPError as exc:
                raise MCPError(f"MCP http request failed: {exc}") from exc
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            if (
                response.status_code == 401
                and attempt == 0
                and token
            ):
                from . import oauth as oauth_mod

                try:
                    refreshed = await oauth_mod.refresh_access_token(
                        self.config, force=True
                    )
                except oauth_mod.OAuthError as exc:
                    refreshed = ""
                    refresh_error = str(exc)
                if refreshed:
                    token = refreshed
                    continue
            break
        assert response is not None
        if response.status_code == 401:
            from .oauth import parse_www_authenticate

            challenge = parse_www_authenticate(
                response.headers.get("www-authenticate", "")
            )
            detail = refresh_error or "OAuth authorization required"
            raise MCPAuthRequired(
                f"MCP http 401: {detail}",
                resource_metadata=challenge.get("resource_metadata", ""),
                scope=challenge.get("scope", ""),
            )
        if response.status_code >= 400:
            raise MCPError(f"MCP http {response.status_code}: {response.text[:200]}")
        message = _decode_body(response)
        if "error" in message:
            raise MCPError(str(message["error"].get("message") or message["error"]))
        return message.get("result")

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._client is None:
            return
        try:
            token = await self._access_token()
        except MCPAuthRequired:
            token = ""
        try:
            await self._client.post(
                self.config.url,
                json={"jsonrpc": "2.0", "method": method, "params": params or {}},
                headers=self._headers(token),
            )
        except httpx.HTTPError:
            pass

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _decode_body(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except ValueError:
                    continue
        raise MCPError("MCP http returned an empty event stream")
    try:
        return response.json()
    except ValueError as exc:
        raise MCPError(f"MCP http returned invalid JSON: {response.text[:200]}") from exc


class MCPConnection:
    """One connected server: initialize handshake + tool list/call."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.transport: StdioTransport | HttpTransport | None = None
        self.tools: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self.instructions = ""

    async def connect(self) -> None:
        self.transport = (
            StdioTransport(self.config) if self.config.transport == "stdio" else HttpTransport(self.config)
        )
        await self.transport.start()
        if isinstance(self.transport, StdioTransport):
            result = await self.transport.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "agentsuper", "version": "1"},
                },
            )
        else:
            result = getattr(self.transport, "initialize_result", {})
        self.instructions = str(result.get("instructions") or "") if isinstance(result, dict) else ""
        await self.transport.notify("notifications/initialized", {})
        self.tools = await self.list_tools()
        self.prompts = await self.list_prompts()

    async def list_tools(self) -> list[dict[str, Any]]:
        if self.transport is None:
            return []
        result = await self.transport.request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        return [tool for tool in tools or [] if isinstance(tool, dict) and tool.get("name")]

    async def list_prompts(self) -> list[dict[str, Any]]:
        """List server prompts (best effort; servers may not support prompts)."""
        if self.transport is None:
            return []
        try:
            result = await self.transport.request("prompts/list", {})
        except MCPError as exc:
            logger.debug("MCP %s prompts/list unavailable: %s", self.config.name, exc)
            return []
        prompts = result.get("prompts") if isinstance(result, dict) else None
        return [item for item in prompts or [] if isinstance(item, dict) and item.get("name")]

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.transport is None:
            raise MCPError(f"MCP server {self.config.name} is not connected")
        result = await self.transport.request(
            "prompts/get", {"name": name, "arguments": arguments or {}}
        )
        return result if isinstance(result, dict) else {}

    async def list_resources(self) -> list[dict[str, Any]]:
        if self.transport is None:
            return []
        result = await self.transport.request("resources/list", {})
        resources = result.get("resources") if isinstance(result, dict) else None
        return [item for item in resources or [] if isinstance(item, dict)]

    async def read_resource(self, uri: str) -> list[dict[str, Any]]:
        if self.transport is None:
            raise MCPError(f"MCP server {self.config.name} is not connected")
        result = await self.transport.request("resources/read", {"uri": uri})
        contents = result.get("contents") if isinstance(result, dict) else None
        return [item for item in contents or [] if isinstance(item, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if self.transport is None:
            raise MCPError(f"MCP server {self.config.name} is not connected")
        result = await self.transport.request("tools/call", {"name": name, "arguments": arguments})
        return _flatten_result(result)

    async def close(self) -> None:
        if self.transport is not None:
            await self.transport.close()
            self.transport = None


def _flatten_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, dict) and item.get("type") == "resource":
                    parts.append(str(item.get("resource") or ""))
            if parts:
                return "\n".join(parts)
        structured = result.get("structuredContent")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False) if result is not None else ""


__all__ = [
    "HttpTransport",
    "MCPAuthRequired",
    "MCPConnection",
    "MCPError",
    "PROTOCOL_VERSION",
    "StdioTransport",
]
