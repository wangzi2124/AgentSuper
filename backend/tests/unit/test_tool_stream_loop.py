from __future__ import annotations

import asyncio
import json

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.delimiters import render_tool_call
from harness.tool_adapter import ToolAdapter
from models.stream import StreamResult


class _GatedStreamModel:
    tier = "fake"
    name = "fallback-model"

    def __init__(self):
        self.called = asyncio.Event()
        self.stream_calls = 0
        self.turn = 0

    async def raw_stream(self, messages, *, tools=None, on_text=None):
        self.stream_calls += 1
        self.turn += 1
        if self.turn == 1:
            on_text(render_tool_call("echo", {"text": "x"}))
            await asyncio.wait_for(self.called.wait(), timeout=1.0)
            on_text("done")
            return StreamResult(content="done", finish_reason="stop")
        return StreamResult(content="finished", finish_reason="stop")

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        raise AssertionError("complete path must not be used for fallback streaming")


@pytest.mark.asyncio
async def test_fallback_stream_executes_tool_before_stream_finishes():
    model = _GatedStreamModel()

    def echo(text: str = "") -> str:
        model.called.set()
        return f"echo:{text}"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="echo", description="echo", fn=echo)
    )

    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        registry,
        adapter=ToolAdapter(model, mode="fallback"),
        stream=True,
    )

    assert model.stream_calls == 2
    assert out["tool_trace"][0]["output"] == "echo:x"
    assert out["tool_history"][1]["tool_call_id"]
    assert out["text"] == "finished"
