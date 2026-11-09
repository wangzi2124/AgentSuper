"""Agent ``request`` / ``steps`` runtime wiring."""

from __future__ import annotations

import asyncio

from agents.registry import request_kwargs
from execution.tool_registry import ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.types import ChatResult


def test_request_kwargs_allows_only_safe_sampling_keys():
    assert request_kwargs(None) is None
    assert request_kwargs({}) is None
    assert request_kwargs(
        {
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 40,
            "max_tokens": 128,
            "stop": ["\n"],
            "seed": 7,
            "response_format": {"type": "json_object"},
            "model": "evil/model",
            "messages": [],
            "tools": [],
            "api_key": "secret",
            "stream": True,
            "unknown": 1,
            "n": 2,
            "max_completion_tokens": 128,
            "timeout": 30,
            "extra_body": {"model": "evil/model"},
            "drop": None,
        }
    ) == {
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 40,
        "max_tokens": 128,
        "stop": ["\n"],
        "seed": 7,
        "response_format": {"type": "json_object"},
    }


def test_request_kwargs_returns_none_when_only_unknown_keys():
    assert request_kwargs({"unknown": 1, "model": "evil/model"}) is None


class _RawModel:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def raw_ainvoke(self, messages, *, tools=None, **kwargs):
        self.kwargs = kwargs
        return ChatResult(content="ok", finish_reason="stop", model="fake")

    async def raw_stream(self, messages, *, tools=None, on_text=None, **kwargs):
        self.kwargs = kwargs
        if on_text:
            on_text("ok")
        return ChatResult(content="ok", finish_reason="stop", model="fake")


def test_tool_adapter_forwards_request_params_complete():
    model = _RawModel()
    adapter = ToolAdapter(model, mode="native", model_id="test/model")
    result = asyncio.run(
        adapter.complete(
            [{"role": "user", "content": "hi"}], None, {"temperature": 0.2}
        )
    )
    assert result.content == "ok"
    assert model.kwargs == {"temperature": 0.2}


def test_tool_adapter_forwards_request_params_stream():
    model = _RawModel()
    adapter = ToolAdapter(model, mode="native", model_id="test/model")

    async def run():
        events = []
        async for event in adapter.stream(
            [{"role": "user", "content": "hi"}], None, {"top_p": 0.9}
        ):
            events.append(event)
        return events

    events = asyncio.run(run())
    assert any(type(event).__name__ == "Done" for event in events)
    assert model.kwargs == {"top_p": 0.9}


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[dict | None] = []

    async def complete(self, messages, tools=None, request_params=None):
        self.calls.append(request_params)
        return ChatResult(content="done", finish_reason="stop", model="fake")


class _FakeModel:
    def model_name(self) -> str:
        return "fake"


def test_run_tool_loop_passes_request_params_to_adapter():
    adapter = _FakeAdapter()
    result = asyncio.run(
        run_tool_loop(
            _FakeModel(),
            [{"role": "user", "content": "hi"}],
            ToolRegistry(),
            adapter=adapter,
            request_params={"temperature": 0.4},
        )
    )
    assert result["text"] == "done"
    assert adapter.calls == [{"temperature": 0.4}]
