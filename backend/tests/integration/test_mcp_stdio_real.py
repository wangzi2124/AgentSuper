"""G-04: real stdio MCP handshake against a spawned Python server."""

from __future__ import annotations

import asyncio
import sys
import textwrap

from mcp.client import MCPConnection
from mcp.config import MCPServerConfig


def _server_script(path):
    path.write_text(
        textwrap.dedent(
            """
            import json, sys

            def send(msg):
                sys.stdout.write(json.dumps(msg) + "\\n")
                sys.stdout.flush()

            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                msg = json.loads(line)
                method = msg.get("method")
                rid = msg.get("id")
                if method == "initialize":
                    send({"jsonrpc": "2.0", "id": rid, "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1"},
                        "instructions": "Be concise.",
                    }})
                elif method == "tools/list":
                    send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                        {"name": "echo", "description": "echo", "inputSchema": {
                            "type": "object", "properties": {"text": {"type": "string"}}
                        }}
                    ]}})
                elif method == "tools/call":
                    args = (msg.get("params") or {}).get("arguments") or {}
                    send({"jsonrpc": "2.0", "id": rid, "result": {
                        "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}]
                    }})
                elif method == "resources/list":
                    send({"jsonrpc": "2.0", "id": rid, "result": {"resources": []}})
                elif rid is not None:
                    send({"jsonrpc": "2.0", "id": rid, "result": {}})
            """
        ),
        encoding="utf-8",
    )


def test_real_stdio_initialize_and_call(tmp_path):
    script = tmp_path / "server.py"
    _server_script(script)
    config = MCPServerConfig(
        name="fake-stdio",
        transport="stdio",
        command=sys.executable,
        args=(str(script),),
        timeout=20,
    )
    connection = MCPConnection(config)

    async def run():
        await connection.connect()
        assert connection.instructions == "Be concise."
        assert [tool["name"] for tool in connection.tools] == ["echo"]
        output = await connection.call_tool("echo", {"text": "hi"})
        await connection.close()
        return output

    assert asyncio.run(run()) == "echo:hi"
