"""MCP 客户端桥接（对齐 agent.md §9 execution/tools/mcp_client.py）。

把 `mcp.adapter` 的 `MCPServer` 暴露的工具包装为可注册工具：每个 MCP 工具注册为
`mcp_<name>`，scope 由调用方指定。未启用/未注册时调用返回可读错误，绝不抛异常。

MCP 传输（stdio/SSE/HTTP）由 `mcp.adapter.create_mcp_server` 负责；本模块只做
「远端工具 → 本地 ToolRegistry」的桥接，保持执行层与协议实现解耦。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

MCP_TOOL_PREFIX = "mcp_"


class MCPToolClient:
    """轻量 MCP 工具客户端：列出/调用远端工具。"""

    def __init__(self, server: Any | None = None) -> None:
        self.server = server

    @property
    def available(self) -> bool:
        return bool(self.server and getattr(self.server, "enabled", False))

    def list_tools(self) -> list[str]:
        if not self.server:
            return []
        try:
            return list(self.server.list())
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP list_tools failed: %s", e)
            return []

    def call(self, name: str, **kwargs: Any) -> str:
        if not self.server:
            return f"❌ MCP server 未配置：无法调用 {name}"
        try:
            result = self.server.call(name, **kwargs)
        except Exception as e:  # noqa: BLE001
            detail = str(e) or repr(e)
            return f"❌ MCP 工具调用失败（{name}）：{detail}"
        return result if isinstance(result, str) else str(result)


def make_mcp_tool_callable(client: MCPToolClient, tool_name: str) -> Callable[..., str]:
    """为单个 MCP 工具生成可注册的本地函数 `tool(**kwargs) -> str`。"""

    def _mcp_tool(**kwargs: Any) -> str:
        return client.call(tool_name, **kwargs)

    _mcp_tool.__name__ = f"{MCP_TOOL_PREFIX}{tool_name}"
    _mcp_tool.__doc__ = f"[MCP] 调用远端工具 {tool_name}。"
    return _mcp_tool


def register_mcp_tools(
    registry: Any,
    client: MCPToolClient,
    *,
    scope: str = "global",
    require_approval: bool = True,
    names: "set[str] | None" = None,
) -> list[str]:
    """把远端 MCP 工具注册进 `ToolRegistry`（前缀 `mcp_`），返回注册名列表。"""
    registered: list[str] = []
    for tool_name in client.list_tools():
        local_name = f"{MCP_TOOL_PREFIX}{tool_name}"
        if names is not None and local_name not in names:
            continue
        if registry.get(local_name) is not None:
            logger.debug("skip duplicate MCP tool: %s", local_name)
            continue
        registry.register_function(
            make_mcp_tool_callable(client, tool_name),
            name=local_name,
            description=f"[MCP] 调用远端工具 {tool_name}。",
            scope=scope,
            require_approval=require_approval,
        )
        registered.append(local_name)
    return registered


__all__ = [
    "MCP_TOOL_PREFIX",
    "MCPToolClient",
    "make_mcp_tool_callable",
    "register_mcp_tools",
]
