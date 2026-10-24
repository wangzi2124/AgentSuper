"""Empty Ollama/thinking replies must not loop to max rounds."""

from __future__ import annotations

import pytest

from execution.tool_registry import ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return ChatResult(content="", finish_reason="stop")


@pytest.mark.asyncio
async def test_empty_replies_cap_after_two_nudges():
    model = _ScriptedModel([ChatResult(content="", finish_reason="stop") for _ in range(10)])
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        ToolRegistry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert model.calls == 4  # 3 loop + 1 final call
    assert out["rounds"] == 4
    assert "模型未产出可展示内容" in out["text"]


@pytest.mark.asyncio
async def test_two_empty_replies_then_answer_still_recovers():
    model = _ScriptedModel(
        [
            ChatResult(content="", finish_reason="stop"),
            ChatResult(content="", finish_reason="stop"),
            ChatResult(content="recovered", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        ToolRegistry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert out["text"] == "recovered"
    assert out["rounds"] == 3
