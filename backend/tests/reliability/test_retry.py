from __future__ import annotations

import asyncio

import pytest

from execution.tool_registry import ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import ChatResult


class _RateLimited(Exception):
    status_code = 429


class _FlakyModel:
    tier = "fake"

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls < 3:
            raise _RateLimited("slow down")
        return ChatResult(content="ok", finish_reason="stop")


@pytest.mark.asyncio
async def test_429_is_retried_then_succeeds(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("harness.retry.asyncio.sleep", no_sleep)
    model = _FlakyModel()
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        ToolRegistry(),
        adapter=ToolAdapter(model, mode="native"),
    )
    assert model.calls == 3
    assert out["text"] == "ok"


@pytest.mark.asyncio
async def test_cancelled_error_is_not_retried():
    class _Cancelled:
        tier = "fake"
        calls = 0

        async def ainvoke(self, messages, *, tools=None, **kwargs):
            self.calls += 1
            raise asyncio.CancelledError()

    model = _Cancelled()
    with pytest.raises(asyncio.CancelledError):
        await run_tool_loop(
            model,
            [{"role": "user", "content": "go"}],
            ToolRegistry(),
            adapter=ToolAdapter(model, mode="native"),
        )
    assert model.calls == 1
