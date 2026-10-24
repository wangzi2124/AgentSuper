from __future__ import annotations

import json
from pathlib import Path

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult, ToolCall


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script):
        self.script = list(script)

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return self.script.pop(0)


def _call(call_id="c1", name="big", args=None):
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments=json.dumps(args or {}),
            )
        ],
        finish_reason="tool-calls",
    )


def _registry(output: str = "x" * 100_000) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="big", description="big output", fn=lambda: output)
    )
    return registry


@pytest.mark.asyncio
async def test_tool_loop_bounds_tool_output_and_persists_full_text(monkeypatch, tmp_path):
    monkeypatch.setattr("cognition.tool_output._truncation_dir", lambda: tmp_path)
    model = _ScriptedModel([_call(), ChatResult(content="done", finish_reason="stop")])

    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    output = out["tool_trace"][0]["output"]
    assert "output truncated" in output
    files = list(tmp_path.glob("tool_*.txt"))
    assert files
    assert len(files[0].read_text(encoding="utf-8")) == 100_000


@pytest.mark.asyncio
async def test_tool_loop_prunes_before_the_next_model_call(monkeypatch):
    calls: list[int] = []

    def fake_prune(messages, **kwargs):
        calls.append(len(messages))
        return messages

    monkeypatch.setattr("graph.nodes.base.prune_tool_outputs", fake_prune)
    model = _ScriptedModel(
        [
            _call("c1"),
            _call("c2"),
            ChatResult(content="done", finish_reason="stop"),
        ]
    )

    await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _registry("ok"),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert calls, "prune_tool_outputs must run before the next model call"
