"""G-04: MCP config loading and runtime registration (fake transport)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from execution.tool_registry import ToolRegistry
from mcp import config as config_mod
from mcp import runtime as runtime_mod
from mcp.runtime import MCPRuntime, local_tool_name


def _write_config(tmp_path: Path, servers: dict) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


class FakeConnection:
    instances: list["FakeConnection"] = []

    def __init__(self, config):
        self.config = config
        self.tools = [
            {
                "name": "echo",
                "description": "Echo text",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ]
        self.calls = []
        FakeConnection.instances.append(self)

    async def connect(self):
        return None

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return f"echo:{arguments.get('text', '')}"

    async def close(self):
        self.calls.append(("close", {}))


def _use(tmp_path, monkeypatch, servers: dict) -> None:
    path = _write_config(tmp_path, servers)
    monkeypatch.setattr(config_mod, "config_paths", lambda workspace: [("global", path)])
    monkeypatch.setattr(runtime_mod, "MCPConnection", FakeConnection)
    FakeConnection.instances = []


def test_local_tool_name_sanitizes():
    assert local_tool_name("my server", "read-file") == "mcp_my_server_read_file"


def test_reload_registers_and_disconnect_removes(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, {"demo": {"type": "stdio", "command": "echo"}})
    runtime = MCPRuntime()
    registry = ToolRegistry()

    servers = asyncio.run(runtime.reload(None, registry))
    assert servers[0]["status"] == "connected"
    assert servers[0]["registered"] == ["mcp_demo_echo"]
    tool = registry.get("mcp_demo_echo")
    assert tool is not None
    assert tool.parameters["properties"]["text"]["type"] == "string"

    out = asyncio.run(tool.fn(text="hi"))
    assert out == "echo:hi"

    server = asyncio.run(runtime.disconnect("demo"))
    assert server["status"] == "disconnected"
    assert registry.get("mcp_demo_echo") is None


def test_invalid_transport_reports_error_without_tools(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, {"bad": {"type": "carrier-pigeon", "command": "x"}})
    runtime = MCPRuntime()
    registry = ToolRegistry()
    servers = asyncio.run(runtime.reload(None, registry))
    assert servers[0]["status"] == "error"
    assert "unsupported transport" in servers[0]["error"]
    assert registry.names() == []


def test_disabled_server_is_not_connected(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, {"off": {"command": "echo", "enabled": False}})
    runtime = MCPRuntime()
    registry = ToolRegistry()
    servers = asyncio.run(runtime.reload(None, registry))
    assert servers[0]["status"] == "disabled"
    assert FakeConnection.instances == []


def test_server_config_crud(tmp_path):
    from mcp.config import delete_server, parse_server, read_server, write_server

    write_server('demo', {'type': 'stdio', 'command': 'echo'}, workspace=tmp_path, scope='project')
    stored = read_server('demo', workspace=tmp_path, scope='project')
    assert stored and stored['command'] == 'echo'
    assert parse_server('demo', stored).transport == 'stdio'
    assert parse_server('bad', {'type': 'nope'}).validate()
    assert delete_server('demo', workspace=tmp_path, scope='project')
    assert read_server('demo', workspace=tmp_path, scope='project') is None
    assert not delete_server('demo', workspace=tmp_path, scope='project')
