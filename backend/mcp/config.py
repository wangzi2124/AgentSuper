"""MCP server configuration loading (opencode gap G-04).

Files (project wins by server name):

* ``~/.config/agentsuper/mcp.json``
* ``<workspace>/.agentsuper/mcp.json``

Both ``{"servers": {...}}`` and Claude-style ``{"mcpServers": {...}}`` are
accepted. Each entry is a stdio command or an HTTP endpoint.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: float = 20.0

    def validate(self) -> str | None:
        if self.transport == "stdio" and not self.command:
            return "stdio server requires `command`"
        if self.transport == "http" and not self.url:
            return "http server requires `url`"
        if self.transport not in ("stdio", "http"):
            return f"unsupported transport: {self.transport}"
        return None


def _parse(name: str, raw: Any) -> MCPServerConfig | None:
    if not isinstance(raw, dict):
        return None
    transport = str(raw.get("type") or raw.get("transport") or "stdio").lower()
    if transport in ("streamable-http", "streamable_http", "sse"):
        transport = "http"
    args = raw.get("args") or ()
    if isinstance(args, str):
        args = [args]
    env = raw.get("env") or {}
    headers = raw.get("headers") or {}
    try:
        timeout = float(raw.get("timeout") or 20.0)
    except (TypeError, ValueError):
        timeout = 20.0
    return MCPServerConfig(
        name=str(raw.get("name") or name),
        transport=transport,
        command=str(raw.get("command") or ""),
        args=tuple(str(part) for part in args),
        env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
        url=str(raw.get("url") or ""),
        headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
        enabled=bool(raw.get("enabled", True)),
        timeout=max(1.0, timeout),
    )


def config_paths(workspace: str | Path | None) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = [
        ("global", Path.home() / ".config" / "agentsuper" / "mcp.json"),
    ]
    if workspace:
        paths.append(("project", Path(workspace).expanduser() / ".agentsuper" / "mcp.json"))
    return paths


def load_mcp_configs(workspace: str | Path | None = None) -> dict[str, MCPServerConfig]:
    configs: dict[str, MCPServerConfig] = {}
    for source, path in config_paths(workspace):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            logger.warning("cannot read MCP config %s: %s", path, exc)
            continue
        servers = raw.get("servers") if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            servers = raw.get("mcpServers") if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            continue
        for name, item in servers.items():
            config = _parse(str(name), item)
            if config is not None:
                configs[config.name] = config
    return configs


def store_path(workspace: str | Path | None, scope: str) -> Path:
    if scope == "project":
        if not workspace:
            raise ValueError("project scope requires a workspace")
        return Path(workspace).expanduser() / ".agentsuper" / "mcp.json"
    return Path.home() / ".config" / "agentsuper" / "mcp.json"


def _read_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_store(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_server(
    name: str, *, workspace: str | Path | None = None, scope: str = "global"
) -> dict[str, Any] | None:
    raw = _read_store(store_path(workspace, scope))
    for key in ("servers", "mcpServers"):
        servers = raw.get(key)
        if isinstance(servers, dict) and isinstance(servers.get(name), dict):
            return dict(servers[name])
    return None


def parse_server(name: str, raw: dict[str, Any]) -> MCPServerConfig | None:
    return _parse(name, raw)


def write_server(
    name: str, payload: dict[str, Any], *, workspace: str | Path | None = None, scope: str = "global"
) -> None:
    path = store_path(workspace, scope)
    raw = _read_store(path)
    key = "servers" if "servers" in raw or "mcpServers" not in raw else "mcpServers"
    servers = raw.get(key)
    if not isinstance(servers, dict):
        servers = {}
    item = {k: v for k, v in payload.items() if k not in ("name", "scope")}
    item["name"] = name
    servers[name] = item
    raw[key] = servers
    _write_store(path, raw)


def delete_server(name: str, *, workspace: str | Path | None = None, scope: str = "global") -> bool:
    path = store_path(workspace, scope)
    raw = _read_store(path)
    for key in ("servers", "mcpServers"):
        servers = raw.get(key)
        if isinstance(servers, dict) and name in servers:
            servers.pop(name, None)
            _write_store(path, raw)
            return True
    return False


__all__ = [
    "MCPServerConfig",
    "config_paths",
    "delete_server",
    "load_mcp_configs",
    "parse_server",
    "read_server",
    "store_path",
    "write_server",
]
