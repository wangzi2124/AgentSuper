from __future__ import annotations

import json

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult, ToolCall


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        return self.script.pop(0)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="echo", description="echo", fn=lambda text="": text)
    )
    return registry


def _tool_call() -> ChatResult:
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(id="c1", name="echo", arguments=json.dumps({"text": "x"}))
        ],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_length_continues_once_and_joins_text():
    model = _ScriptedModel(
        [
            ChatResult(content="part1", finish_reason="length"),
            ChatResult(content="part2", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "write"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert out["text"] == "part1part2"
    assert out["rounds"] == 2
    assert out["finish_reason"] == "stop"
    second_call = model.calls[1]["messages"]
    assert second_call[-1]["role"] == "user"
    assert "从中断处继续" in second_call[-1]["content"]


@pytest.mark.asyncio
async def test_length_at_round_limit_keeps_partial_and_notice():
    model = _ScriptedModel(
        [ChatResult(content="partial", finish_reason="length")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "write"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
        max_rounds=1,
    )

    assert out["text"].startswith("partial")
    assert "截断" in out["text"]
    assert out["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_content_filter_returns_safe_text_without_model_content():
    model = _ScriptedModel(
        [ChatResult(content="SECRET", finish_reason="content-filter")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "SECRET" not in out["text"]
    assert "安全策略" in out["text"]
    assert out["finish_reason"] == "content-filter"


@pytest.mark.asyncio
async def test_error_finish_reason_returns_explicit_error_text():
    model = _ScriptedModel([ChatResult(content="", finish_reason="error")])
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "模型调用失败" in out["text"]
    assert out["finish_reason"] == "error"


@pytest.mark.asyncio
async def test_length_on_max_rounds_final_call_keeps_tool_history():
    model = _ScriptedModel(
        [
            _tool_call(),
            ChatResult(content="partial", finish_reason="length"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
        max_rounds=1,
    )

    assert out["finish_reason"] == "length"
    assert "partial" in out["text"]
    assert "截断" in out["text"]
    assert out["tool_history"]
