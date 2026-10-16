"""MCP 适配器（M5 · 规模化）。

最小可用的 MCP（Model Context Protocol）适配器，保持**无硬依赖**：
- 本地未安装 `mcp` 时自动降级（`create_mcp_server` 返回 `enabled=False` 的 server）；
- 提供工具注册/列举/调用与 stdio 传输配置载体；
- 真实 MCP 运行时可用时，可在此基础上对接其 transport（§12 M5）。

`MCPServer` 是纯内存工具容器，便于测试与离线演示；`MCPClient` 面向它调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """MCP 远端工具描述。"""

    name: str
    description: str
    fn: Callable[..., Any]
    schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPTransportConfig:
    """MCP 传输配置（stdio 示例；SSE/HTTP 可扩展）。"""

    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)


class MCPServer:
    """内存 MCP 工具容器。`enabled=False` 时注册被忽略、调用抛错。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._tools: dict[str, MCPTool] = {}

    def register(self, tool: MCPTool) -> None:
        if not self.enabled:
            return
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    def call(self, name: str, **kwargs: Any) -> Any:
        if not self.enabled:
            raise RuntimeError(f"MCP server disabled: cannot call {name}")
        tool = self._tools.get(name)
        if tool is None:
            raise RuntimeError(f"MCP tool not found: {name}")
        return tool.fn(**kwargs)


class MCPClient:
    """面向 `MCPServer` 的调用客户端（可注入 server，便于单测）。"""

    def __init__(self, server: MCPServer | None = None) -> None:
        self.server = server

    def call(self, name: str, **kwargs: Any) -> Any:
        if not self.server:
            raise RuntimeError("MCP server not configured")
        return self.server.call(name, **kwargs)

    def list_tools(self) -> list[str]:
        return self.server.list() if self.server else []


def _mcp_available() -> bool:
    """检测**真实** MCP 运行时是否可用。

    注意：本目录自身是 `mcp` 的**命名空间包**，故 `import mcp` 恒成立；必须按
    解析路径判断，只有第三方 mcp SDK（位于本目录之外）才算可用。
    """
    try:
        import mcp  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    from pathlib import Path

    here = Path(__file__).resolve().parent
    locations: list[str] = list(getattr(mcp, "__path__", []) or [])
    if getattr(mcp, "__file__", None):
        locations.append(mcp.__file__)
    for loc in locations:
        resolved = Path(loc).resolve()
        if resolved != here and here not in resolved.parents:
            return True
    return False


def create_mcp_server(
    enabled: bool = True,
    *,
    transport: MCPTransportConfig | None = None,
) -> MCPServer:
    """创建 MCP server。`enabled=True` 但运行时缺失时自动降级为 disabled。"""
    if enabled and not _mcp_available():
        logger.info("MCP runtime not installed; MCP server degraded to disabled")
        enabled = False
    server = MCPServer(enabled=enabled)
    if transport is not None:
        server.transport = transport  # type: ignore[attr-defined]
    return server


def create_mcp_client(
    enabled: bool = True,
    *,
    transport: MCPTransportConfig | None = None,
) -> MCPClient:
    """创建 MCP 客户端（绑定一个新 server）。"""
    return MCPClient(create_mcp_server(enabled, transport=transport))


__all__ = [
    "MCPClient",
    "MCPServer",
    "MCPTool",
    "MCPTransportConfig",
    "create_mcp_client",
    "create_mcp_server",
]
