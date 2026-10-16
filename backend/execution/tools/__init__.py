"""执行层工具集合（对齐 agent.md §9 execution/tools/）。

- `file_tools`：VirtualFileSystem 包装的读写/编辑/检索工具
- `web_tools`：带 SSRF 防护的联网检索与抓取
- `mcp_client`：把 MCP 远端工具桥接为本地工具

- `envelope`：工具结果信封（{title, metadata, output}）与统一 `unwrap` 出口（中性实现）
"""

from .envelope import envelope, unwrap  # noqa: F401

from .file_tools import make_file_tools, register_file_tools
from .mcp_client import MCPToolClient, register_mcp_tools
from .web_tools import make_web_tools, register_web_tools, web_fetch, web_search

__all__ = [
    "unwrap",
    "envelope",
    "make_file_tools",
    "register_file_tools",
    "make_web_tools",
    "register_web_tools",
    "web_fetch",
    "web_search",
    "MCPToolClient",
    "register_mcp_tools",
]
