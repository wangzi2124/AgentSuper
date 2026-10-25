"""File/directory tasks without tool evidence must not be answered from memory."""

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

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return self.script.pop(0)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="ls",
            description="list files",
            fn=lambda path=".": "alpha.txt\nbeta.json",
        )
    )
    return registry


def _tool_call() -> ChatResult:
    return ChatResult(
        content="",
        tool_calls=[ToolCall(id="c1", name="ls", arguments=json.dumps({"path": "D:/"}))],
        finish_reason="tool-calls",
    )


@pytest.mark.asyncio
async def test_file_task_without_tool_is_not_fabricated():
    model = _ScriptedModel(
        [
            ChatResult(
                content="D盘有这些文件：abc.txt、def.json、ghi.md",
                finish_reason="stop",
            )
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "在 D盘有哪些文件"}],
        _registry(),
        adapter=ToolAdapter(model, mode="fallback", model_id="ollama_chat/deepseek-r1:7b"),
    )

    assert "无法确认" in out["text"]
    assert "abc.txt" not in out["text"]
    assert not out["tool_trace"]


@pytest.mark.asyncio
async def test_file_task_with_tool_evidence_is_allowed():
    model = _ScriptedModel(
        [
            _tool_call(),
            ChatResult(content="D盘有 alpha.txt、beta.json", finish_reason="stop"),
        ]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "在 D盘有哪些文件"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert out["tool_trace"], out["text"]
    assert "alpha.txt" in out["text"]
    assert "无法确认" not in out["text"]


@pytest.mark.asyncio
async def test_non_file_task_without_tool_is_not_blocked():
    model = _ScriptedModel(
        [ChatResult(content="GIL 是 Python 的全局解释器锁。", finish_reason="stop")]
    )
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "解释一下 Python 的 GIL"}],
        _registry(),
        adapter=ToolAdapter(model, mode="native"),
    )

    assert "GIL" in out["text"]
    assert "无法确认" not in out["text"]
