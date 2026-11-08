"""MCP runtime: connect servers, expose tools, report status (gap G-04).

No in-memory stub is used on this path. A server that cannot start is marked
``error`` and registers zero tools 鈥?never a fake success.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from execution.tool_registry import ToolRegistry

from .client import MCPConnection, MCPError
from .config import MCPServerConfig, load_mcp_configs

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9_]")


def _safe(value: str) -> str:
    return _SAFE.sub("_", str(value or "").strip())


def local_tool_name(server: str, tool: str) -> str:
    return f"mcp_{_safe(server)}_{_safe(tool)}"


@dataclass
class ServerState:
    config: MCPServerConfig
    status: str = "disconnected"
    error: str = ""
    tools: list[str] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)
    instructions: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "transport": self.config.transport,
            "enabled": self.config.enabled,
            "status": self.status,
            "error": self.error,
            "tools": list(self.tools),
            "resources": list(self.resources),
            "registered": list(self.registered),
            "has_instructions": bool(self.instructions),
        }


class MCPRuntime:
    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}
        self._states: dict[str, ServerState] = {}
        self._workspace: str | None = None
        self._registry: ToolRegistry | None = None
        self._lock = asyncio.Lock()

    def _unregister(self) -> None:
        if self._registry is None:
            return
        for state in self._states.values():
            for name in state.registered:
                self._registry.unregister(name)
            state.registered = []

    async def _close_all(self) -> None:
        for name, connection in list(self._connections.items()):
            try:
                await connection.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("closing MCP %s failed: %s", name, exc)
        self._connections.clear()

    async def reload(
        self, workspace: str | Path | None, registry: ToolRegistry
    ) -> list[dict[str, Any]]:
        async with self._lock:
            self._unregister()
            await self._close_all()
            self._workspace = str(workspace) if workspace else None
            self._registry = registry
            configs = load_mcp_configs(workspace)
            self._states = {name: ServerState(config) for name, config in configs.items()}
            for name, config in configs.items():
                if not config.enabled:
                    self._states[name].status = "disabled"
                    continue
                problem = config.validate()
                if problem:
                    self._states[name].status = "error"
                    self._states[name].error = problem
                    continue
                connection = MCPConnection(config)
                try:
                    await connection.connect()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("MCP %s connect failed: %s", name, exc)
                    self._states[name].status = "error"
                    self._states[name].error = str(exc) or repr(exc)
                    continue
                self._connections[name] = connection
                self._states[name].status = "connected"
                self._states[name].tools = [
                    str(tool.get("name")) for tool in connection.tools
                ]
                self._states[name].instructions = getattr(connection, "instructions", "")
                self._states[name].resources = await self._list_resources(connection)
                self._states[name].registered = self._register_tools(
                    registry, name, connection
                )
            return self.status()

    async def _list_resources(self, connection: MCPConnection) -> list[dict[str, Any]]:
        try:
            return await connection.list_resources()
        except Exception as exc:  # noqa: BLE001
            logger.debug("MCP %s resources/list unavailable: %s", connection.config.name, exc)
            return []

    def _register_tools(
        self, registry: ToolRegistry, server: str, connection: MCPConnection
    ) -> list[str]:
        registered: list[str] = []
        for tool in connection.tools:
            remote = str(tool.get("name"))
            local = local_tool_name(server, remote)
            if registry.get(local) is not None:
                logger.warning("MCP tool name collision, skipping: %s", local)
                continue
            schema = tool.get("inputSchema") or tool.get("input_schema") or tool.get("parameters") or {}
            if not isinstance(schema, dict):
                schema = {}

            async def _call(_remote: str = remote, **kwargs: Any) -> str:
                return await connection.call_tool(_remote, kwargs)

            _call.__name__ = local
            _call.__doc__ = str(tool.get("description") or f"[MCP:{server}] {remote}")
            registry.register_function(
                _call,
                name=local,
                description=_call.__doc__,
                scope="global",
                require_approval=True,
                mutating=True,
                parameters=schema,
            )
            registered.append(local)
        return registered

    async def connect(self, name: str, registry: ToolRegistry | None = None) -> dict[str, Any]:
        async with self._lock:
            state = self._states.get(name)
            if state is None:
                raise MCPError(f"unknown MCP server: {name}")
            if name in self._connections:
                return state.as_dict()
            reg = registry or self._registry
            if reg is None:
                raise MCPError("MCP runtime has no tool registry yet")
            connection = MCPConnection(state.config)
            await connection.connect()
            self._connections[name] = connection
            state.status = "connected"
            state.error = ""
            state.tools = [str(tool.get("name")) for tool in connection.tools]
            state.instructions = getattr(connection, "instructions", "")
            state.resources = await self._list_resources(connection)
            state.registered = self._register_tools(reg, name, connection)
            return state.as_dict()

    async def disconnect(self, name: str) -> dict[str, Any]:
        async with self._lock:
            state = self._states.get(name)
            if state is None:
                raise MCPError(f"unknown MCP server: {name}")
            connection = self._connections.pop(name, None)
            if connection is not None:
                await connection.close()
            if self._registry is not None:
                for local in state.registered:
                    self._registry.unregister(local)
            state.registered = []
            state.tools = []
            state.status = "disabled" if not state.config.enabled else "disconnected"
            return state.as_dict()

    def status(self) -> list[dict[str, Any]]:
        return [state.as_dict() for state in self._states.values()]

    def tools(self, name: str) -> list[str]:
        state = self._states.get(name)
        if state is None:
            raise MCPError(f"unknown MCP server: {name}")
        return list(state.tools)

    def resources(self, name: str) -> list[dict[str, Any]]:
        state = self._states.get(name)
        if state is None:
            raise MCPError(f"unknown MCP server: {name}")
        return list(state.resources)

    async def read_resource(self, name: str, uri: str) -> list[dict[str, Any]]:
        connection = self._connections.get(name)
        if connection is None:
            raise MCPError(f"MCP server {name} is not connected")
        return await connection.read_resource(uri)

    def instructions(self) -> str:
        parts = [
            f"# MCP server: {state.config.name}\n{state.instructions.strip()}"
            for state in self._states.values()
            if state.status == "connected" and state.instructions.strip()
        ]
        return "\n\n".join(parts)

    def server_instructions(self, name: str) -> str:
        state = self._states.get(name)
        if state is None:
            raise MCPError(f"unknown MCP server: {name}")
        return state.instructions

    @property
    def workspace(self) -> str | None:
        return self._workspace

    async def close(self) -> None:
        async with self._lock:
            self._unregister()
            await self._close_all()


_RUNTIME: MCPRuntime | None = None


def get_mcp_runtime() -> MCPRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = MCPRuntime()
    return _RUNTIME


__all__ = ["MCPRuntime", "ServerState", "get_mcp_runtime", "local_tool_name"]

