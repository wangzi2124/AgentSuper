"""L1 单元测试：execution/tools（file / web / mcp），agent.md §9。"""

import pytest

from execution.filesystem import VirtualFileSystem
from execution.tool_registry import ToolRegistry
from execution.tools.file_tools import make_file_tools, register_file_tools
from execution.tools.mcp_client import MCPToolClient, register_mcp_tools
from execution.tools.web_tools import register_web_tools, web_fetch, web_search
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
    assert set(names) == {"web_search", "web_fetch"}
    assert reg.get("web_search").scope == "global"
    assert reg.needs_approval("web_search") is False


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
