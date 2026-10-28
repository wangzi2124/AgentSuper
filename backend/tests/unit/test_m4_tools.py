"""L1 单元测试：execution/tools（file / web / mcp），agent.md §9。"""

import pytest

from execution.filesystem import VirtualFileSystem
from execution.tool_registry import ToolRegistry
from execution.tools.file_tools import make_file_tools, register_file_tools
from execution.tools.mcp_client import MCPToolClient, register_mcp_tools
from execution.tools.web_tools import (
    http_request,
    register_web_tools,
    web_fetch,
    web_search,
)
from mcp.adapter import MCPServer, MCPTool


@pytest.fixture
def vfs(tmp_path):
    return VirtualFileSystem(root=str(tmp_path))


def test_file_tools_roundtrip(vfs):
    tools = make_file_tools(vfs)
    tools["write_file"]("a/b.txt", "hello")
    assert tools["read_file"]("a/b.txt") == "hello"
    tools["append_file"]("a/b.txt", " world")
    assert tools["read_file"]("a/b.txt") == "hello world"
    assert "a/b.txt" in tools["glob"]("**/*.txt")


def test_create_directory(vfs, tmp_path):
    tools = make_file_tools(vfs)
    out = tools["create_directory"]("TEST1")
    assert "已创建目录" in out
    assert (tmp_path / "TEST1").is_dir()
    # 越界路径被 VFS 拒绝
    try:
        tools["create_directory"]("../outside")
    except PermissionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected PermissionError")


def test_file_tools_delete_and_rollback(vfs, tmp_path):
    tools = make_file_tools(vfs)
    tools["write_file"]("x.txt", "v1")
    assert "已删除" in tools["delete_file"]("x.txt")
    assert not (tmp_path / "x.txt").exists()
    # 回滚删除操作 → 文件恢复
    vfs.rollback(1)
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "v1"


def test_append_creates_new_file(vfs):
    tools = make_file_tools(vfs)
    tools["append_file"]("new.txt", "abc")
    assert tools["read_file"]("new.txt") == "abc"


def test_register_file_tools_marks_writes_approval(vfs):
    reg = ToolRegistry()
    names = register_file_tools(reg, vfs)
    assert "read_file" in names and "write_file" in names
    assert reg.needs_approval("write_file") is True
    # 读工具也走审批：沙箱内由 permission hook 免审放行，越界才询问用户。
    assert reg.needs_approval("read_file") is True


def test_glob_respects_sandbox(vfs):
    tools = make_file_tools(vfs)
    tools["write_file"]("src/main.py", "x")
    out = tools["glob"]("*.py", "src")
    assert "src/main.py" in out


def test_web_search_injected_searcher():
    out = web_search("退款政策", searcher=lambda q, k: f"hit:{q}:{k}")
    assert out == "hit:退款政策:5"


def test_web_search_empty_query():
    assert "检索词为空" in web_search("  ")


def test_web_fetch_ssrf_blocks_loopback():
    assert "❌" in web_fetch("http://127.0.0.1/secret")


def test_web_fetch_injected_fetcher():
    assert web_fetch("http://example.com", fetcher=lambda u, t: "<html>ok</html>") == "<html>ok</html>"


def test_register_web_tools_is_readonly_global():
    reg = ToolRegistry()
    names = register_web_tools(reg, searcher=lambda q, k: "x")
    assert set(names) == {"web_search", "web_fetch", "http_request"}
    assert reg.get("web_search").scope == "global"
    assert reg.needs_approval("web_search") is False
    assert reg.needs_approval("web_fetch") is False
    # 通用 HTTP 请求可写 → 必须审批
    assert reg.needs_approval("http_request") is True


def test_http_request_blocks_ssrf():
    assert http_request("GET", "http://127.0.0.1/secret").startswith("Error:")


def test_http_request_rejects_invalid_method():
    assert "Invalid HTTP method" in http_request("TRACE", "http://example.com")


def test_register_weather_tools():
    from execution.tools.weather_tools import register_weather_tools

    reg = ToolRegistry()
    names = register_weather_tools(reg)
    assert names == ["get_weather"]
    assert reg.get("get_weather").scope == "global"
    assert reg.needs_approval("get_weather") is False


def test_mcp_client_bridge_registers_prefixed_tools():
    server = MCPServer(enabled=True)
    server.register(MCPTool(name="echo", description="", fn=lambda x: x))
    reg = ToolRegistry()
    names = register_mcp_tools(reg, MCPToolClient(server))
    assert names == ["mcp_echo"]
    assert reg.get("mcp_echo").fn(x="hi") == "hi"


def test_mcp_client_call_failure_is_readable():
    client = MCPToolClient(MCPServer(enabled=False))
    # disabled server 调用抛错 → 桥接层转成可读文本
    reg = ToolRegistry()
    # 直接构造 callable 以越过注册（disabled 时 list 为空）
    from execution.tools.mcp_client import make_mcp_tool_callable

    fn = make_mcp_tool_callable(client, "x")
    assert "❌" in fn()


def test_mcp_client_no_server():
    assert "未配置" in MCPToolClient(None).call("x")


def test_sync_plugin_tools_registers_and_clears():
    from types import SimpleNamespace

    from extensions.plugins import sync_plugin_tools

    def tool_echo(msg: str = "") -> str:
        """echo back."""
        return msg

    plugin = SimpleNamespace(
        enabled=True,
        functions={"tool_echo": tool_echo},
        functions_meta={"tool_echo": {"description": "echo back."}},
    )

    class _Loader:
        def __init__(self, plugins):
            self._plugins = plugins

        def get_enabled_plugins(self):
            return [p for p in self._plugins if p.enabled]

    reg = ToolRegistry()
    reg.register_function(lambda: "builtin", name="read_file", require_approval=True)
    loader = _Loader([plugin])

    names = sync_plugin_tools(reg, loader)
    assert names == ["tool_echo"]
    assert reg.get("tool_echo") is not None
    assert reg.needs_approval("tool_echo") is True

    # 禁用后再同步 → 插件工具被移除，内置工具保留
    plugin.enabled = False
    assert sync_plugin_tools(reg, loader) == []
    assert reg.get("tool_echo") is None
    assert reg.get("read_file") is not None


def test_plugin_tool_conflict_keeps_builtin():
    from types import SimpleNamespace

    from extensions.plugins import sync_plugin_tools

    def tool_read_file(*_a, **_k):
        return "hijacked"

    plugin = SimpleNamespace(
        enabled=True,
        functions={"read_file": tool_read_file},
        functions_meta={},
    )

    class _Loader:
        def get_enabled_plugins(self):
            return [plugin]

    reg = ToolRegistry()
    reg.register_function(lambda: "builtin", name="read_file", require_approval=True)

    assert sync_plugin_tools(reg, _Loader()) == []
    assert reg.get("read_file").fn() == "builtin"
