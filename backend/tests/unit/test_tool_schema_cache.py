from __future__ import annotations

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import build_tool_defs
from harness.schema import MAX_TOOL_DEFS


def _registry(count: int) -> ToolRegistry:
    registry = ToolRegistry()
    for i in range(count):
        registry.register(
            ToolDefinition(
                name=f"tool_{i}",
                description=f"tool {i}",
                fn=lambda: "ok",
                scope="global" if i < 3 else "location",
            )
        )
    return registry


def test_tool_defs_are_capped_and_keep_business_tools():
    defs = build_tool_defs(_registry(MAX_TOOL_DEFS + 5), scope="location")
    names = [d["function"]["name"] for d in defs]

    assert len(defs) == MAX_TOOL_DEFS
    # 业务工具（scope=location）必须先占满名额，global 工具排后
    assert names[:5] == ["tool_3", "tool_4", "tool_5", "tool_6", "tool_7"]
    assert f"tool_{MAX_TOOL_DEFS + 4}" not in names
    assert "tool_0" not in names


def test_skill_tools_do_not_crowd_out_file_and_shell_tools():
    """回归：大量 global 技能工具不能挤掉 location 文件/Shell 工具。"""
    registry = ToolRegistry()
    for name in ("read_file", "write_file", "edit_file", "ls", "grep", "glob", "shell_exec"):
        registry.register(
            ToolDefinition(name=name, description=name, fn=lambda: "ok", scope="location")
        )
    registry.register(
        ToolDefinition(name="task", description="delegate", fn=lambda: "ok", scope="global")
    )
    for i in range(40):
        registry.register(
            ToolDefinition(
                name=f"load_skill_{i}",
                description=f"skill {i}",
                fn=lambda: "ok",
                scope="global",
            )
        )

    names = [d["function"]["name"] for d in build_tool_defs(registry, scope="location")]
    assert len(names) == MAX_TOOL_DEFS
    for required in ("read_file", "write_file", "edit_file", "ls", "grep", "glob", "shell_exec", "task"):
        assert required in names



def test_tool_defs_cache_hits_and_invalidates(monkeypatch):
    registry = _registry(2)
    first = build_tool_defs(registry, scope="location")
    second = build_tool_defs(registry, scope="location")
    assert first == second

    registry.register(
        ToolDefinition(name="tool_new", description="new", fn=lambda: "ok", scope="location")
    )
    third = build_tool_defs(registry, scope="location")
    assert "tool_new" in [d["function"]["name"] for d in third]
