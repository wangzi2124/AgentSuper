"""工具配额已接入 run_tool_loop 执行入口。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import settings
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import _execute_tool_call


def _call(name: str = "t") -> SimpleNamespace:
    return SimpleNamespace(name=name, arguments="{}")


@pytest.mark.asyncio
async def test_rate_limit_blocks_second_tool_call():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="t", description="", fn=lambda: "ok", rate_limit=1)
    )

    first, _, _ = await _execute_tool_call(_call(), "c1", registry, "build")
    second, _, _ = await _execute_tool_call(_call(), "c2", registry, "build")

    assert "超出配额" not in first
    assert "超出配额" in second


@pytest.mark.asyncio
async def test_token_budget_blocks_after_output_consumed():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="t",
            description="",
            fn=lambda: "x" * 200,
            token_budget=30,
        )
    )

    first, _, _ = await _execute_tool_call(_call(), "c1", registry, "build")
    second, _, _ = await _execute_tool_call(_call(), "c2", registry, "build")

    assert "超出配额" not in first
    assert "超出配额" in second


@pytest.mark.asyncio
async def test_single_call_that_overshoots_budget_is_rejected():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="t",
            description="",
            fn=lambda: "x" * 200,
            token_budget=10,
        )
    )

    out, _, _ = await _execute_tool_call(_call(), "c1", registry, "build")
    assert "超出配额" in out


@pytest.mark.asyncio
async def test_global_default_rate_limit_applies_when_tool_has_none(monkeypatch):
    monkeypatch.setattr(settings, "tool_default_rate_limit", 1)
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="t", description="", fn=lambda: "ok"))

    first, _, _ = await _execute_tool_call(_call(), "d1", registry, "build")
    second, _, _ = await _execute_tool_call(_call(), "d2", registry, "build")

    assert "超出配额" not in first
    assert "超出配额" in second


@pytest.mark.asyncio
async def test_tool_explicit_limit_beats_global_default(monkeypatch):
    monkeypatch.setattr(settings, "tool_default_rate_limit", 1)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="t", description="", fn=lambda: "ok", rate_limit=2)
    )

    for i in range(2):
        out, _, _ = await _execute_tool_call(_call(), f"o{i}", registry, "build")
        assert "超出配额" not in out
    third, _, _ = await _execute_tool_call(_call(), "o3", registry, "build")
    assert "超出配额" in third
