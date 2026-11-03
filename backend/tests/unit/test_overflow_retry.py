from __future__ import annotations

from types import SimpleNamespace

import pytest

from execution.tool_registry import ToolRegistry
from graph.nodes.base import run_tool_loop
from models.router import ChatResult


class _FlakyAdapter:
    mode = "native"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        if len(self.calls) == 1:
            raise RuntimeError("maximum context length exceeded")
        return ChatResult(content="ok", finish_reason="stop")


@pytest.mark.asyncio
async def test_overflow_retry_compresses_and_retries_once(monkeypatch):
    adapter = _FlakyAdapter()
    seen: dict = {}

    async def _compress(messages, **kwargs):
        seen["count"] = len(messages)
        return SimpleNamespace(
            triggered=True,
            summary="SUMMARY",
            recent=[{"role": "user", "content": "latest"}],
        )

    monkeypatch.setattr("harness.compression.compress_messages", _compress)

    out = await run_tool_loop(
        SimpleNamespace(tier="fake"),
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
        ],
        ToolRegistry(),
        adapter=adapter,
    )

    assert out["text"] == "ok"
    assert len(adapter.calls) == 2
    assert seen["count"] == 3
    retried = adapter.calls[1]
    assert retried[0]["role"] == "system"
    assert "SUMMARY" in retried[0]["content"]
    assert retried[-1] == {"role": "user", "content": "latest"}


@pytest.mark.asyncio
async def test_overflow_retry_falls_back_to_trim(monkeypatch):
    adapter = _FlakyAdapter()

    async def _boom(messages, **kwargs):
        raise RuntimeError("summary unavailable")

    monkeypatch.setattr("harness.compression.compress_messages", _boom)

    out = await run_tool_loop(
        SimpleNamespace(tier="fake"),
        [{"role": "user", "content": f"m{i}"} for i in range(10)],
        ToolRegistry(),
        adapter=adapter,
    )

    assert out["text"] == "ok"
    assert len(adapter.calls) == 2
    assert len(adapter.calls[1]) < len(adapter.calls[0])
