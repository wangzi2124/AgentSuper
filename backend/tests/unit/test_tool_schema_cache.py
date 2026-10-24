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


def test_tool_defs_are_capped_at_15_and_keep_global_tools():
    defs = build_tool_defs(_registry(20), scope="location")
    names = [d["function"]["name"] for d in defs]

    assert len(defs) == MAX_TOOL_DEFS
    assert names[:3] == ["tool_0", "tool_1", "tool_2"]
    assert "tool_19" not in names


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
