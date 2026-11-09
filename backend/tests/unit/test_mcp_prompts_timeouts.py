"""P0-2: fine-grained MCP timeouts and prompt catalog."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from execution.tool_registry import ToolRegistry
from mcp import config as config_mod
from mcp import runtime as runtime_mod
from mcp.client import MCPConnection, MCPError
from mcp.config import parse_server
from mcp.runtime import MCPRuntime


def test_parse_timeouts_and_resolution():
    cfg = parse_server(
        "demo",
        {
            "command": "echo",
            "timeout": 20,
            "startup_timeout": 5,
            "catalog_timeout": 10,
        },
    )
    assert cfg.startup_timeout == 5
    assert cfg.catalog_timeout == 10
    assert cfg.execution_timeout == 0
    assert cfg.timeout_for("initialize") == 5
    assert cfg.timeout_for("tools/list") == 10
    assert cfg.timeout_for("prompts/list") == 10
    assert cfg.timeout_for("tools/call") == 20


def test_timeout_falls_back_to_base():
    cfg = parse_server("demo", {"command": "echo", "timeout": 12})
    assert cfg.timeout_for("initialize") == 12
    assert cfg.timeout_for("resources/read") == 12


class FakeTransport:
    def __init__(self, fail_prompts: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_prompts = fail_prompts

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "tools/list":
            return {"tools": [{"name": "echo"}]}
        if method == "prompts/list":
            if self.fail_prompts:
                raise MCPError("prompts unsupported")
            return {"prompts": [{"name": "review"}, {"bad": 1}]}
        if method == "prompts/get":
            return {"messages": [{"role": "user", "content": params}]}
        return {}


def test_connection_lists_and_gets_prompts():
    conn = MCPConnection(parse_server("demo", {"command": "echo"}))
    conn.transport = FakeTransport()

    prompts = asyncio.run(conn.list_prompts())
    assert prompts == [{"name": "review"}]

    result = asyncio.run(conn.get_prompt("review", {"file": "a.py"}))
    assert result["messages"][0]["content"] == {
        "name": "review",
        "arguments": {"file": "a.py"},
    }


def test_prompts_list_failure_is_best_effort():
    conn = MCPConnection(parse_server("demo", {"command": "echo"}))
    conn.transport = FakeTransport(fail_prompts=True)
    assert asyncio.run(conn.list_prompts()) == []


class FakeConnection:
    instances: list["FakeConnection"] = []

    def __init__(self, config):
        self.config = config
        self.tools = [{"name": "echo", "inputSchema": {"type": "object"}}]
        self.prompts = [{"name": "review", "description": "Review code"}]
        FakeConnection.instances.append(self)

    async def connect(self):
        return None

    async def call_tool(self, name, arguments):
        return "ok"

    async def get_prompt(self, name, arguments=None):
        return {"messages": [{"role": "user", "content": name}]}

    async def close(self):
        return None


def test_runtime_exposes_prompts(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"demo": {"command": "echo"}}}), encoding="utf-8"
    )
    monkeypatch.setattr(config_mod, "config_paths", lambda workspace: [("global", path)])
    monkeypatch.setattr(runtime_mod, "MCPConnection", FakeConnection)

    runtime = MCPRuntime()
    servers = asyncio.run(runtime.reload(None, ToolRegistry()))
    assert servers[0]["prompts"] == [
        {"name": "review", "description": "Review code"}
    ]
    assert runtime.prompts("demo")[0]["name"] == "review"
    out = asyncio.run(runtime.get_prompt("demo", "review", {"file": "a.py"}))
    assert out["messages"][0]["content"] == "review"
