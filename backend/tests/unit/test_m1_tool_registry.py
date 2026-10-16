"""L1 单元测试：ToolRegistry（agent.md §5.3）。"""

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry


def _fn(a: int, b: str = "x"):
    """示例工具。"""
    return f"{a}-{b}"


def test_register_and_get():
    reg = ToolRegistry()
    reg.register_function(_fn, name="fn", description="d")
    assert reg.get("fn").name == "fn"
    assert "fn" in reg.names()


def test_duplicate_raises():
    reg = ToolRegistry()
    reg.register_function(_fn, name="fn")
    with pytest.raises(ValueError):
        reg.register(ToolDefinition(name="fn", description="", fn=_fn))


def test_scope_filtering():
    reg = ToolRegistry()
    reg.register_function(_fn, name="global_tool", scope="global")
    reg.register_function(_fn, name="loc_tool", scope="location")
    names = [d.name for d in reg.definitions("location")]
    assert "global_tool" in names and "loc_tool" in names
    only_global = [d.name for d in reg.definitions("subagent")]
    assert only_global == ["global_tool"]


def test_needs_approval_defaults_true_for_unknown():
    reg = ToolRegistry()
    assert reg.needs_approval("missing") is True
    reg.register_function(_fn, name="safe", require_approval=False)
    assert reg.needs_approval("safe") is False
