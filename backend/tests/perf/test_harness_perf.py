"""Harness performance gates (design §11.7)."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.delimiters import render_tool_call
from harness.tool_adapter import TextDelta, ToolAdapter
from models.router import ChatResult, TierModel, reset_request_model, set_request_model
from models.stream import StreamResult

_MODEL = (os.getenv("HARNESS_SMOKE_MODEL_A") or "").strip()


class _FakeModel:
    tier = "fake"

    def __init__(self, text="ok"):
        self.text = text

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content=self.text, finish_reason="stop")


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="echo", description="echo", fn=lambda text="": text)
    )
    return registry


@pytest.mark.asyncio
async def test_pf03_fallback_parse_overhead():
    text = render_tool_call("echo", {"text": "x"})
    model = ToolAdapter(_FakeModel(), mode="fallback")
    start = time.perf_counter()
    for _ in range(100):
        await model.complete([{"role": "user", "content": "go"}], [
            {"type": "function", "function": {"name": "echo", "parameters": {}}}
        ])
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.01


@pytest.mark.asyncio
async def test_pf04_fake_concurrency_p95():
    async def one():
        model = _FakeModel("ok")
        return await run_tool_loop(
            model,
            [{"role": "user", "content": "go"}],
            _echo_registry(),
            adapter=ToolAdapter(model, mode="native"),
        )

    start = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(20)))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0


@pytest.mark.skipif(not _MODEL, reason="set HARNESS_SMOKE_MODEL_A for real perf")
@pytest.mark.asyncio
async def test_pf01_real_first_token():
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        adapter = ToolAdapter(model)
        start = time.perf_counter()
        first = None
        async for event in adapter.stream(
            [{"role": "user", "content": "Reply with OK."}], None
        ):
            if isinstance(event, TextDelta) and event.text:
                first = time.perf_counter() - start
                break
    finally:
        reset_request_model(token)

    assert first is not None, "real model produced no text delta"
    assert first < 2.5, f"first token {first:.3f}s >= 2.5s"


@pytest.mark.skipif(not _MODEL, reason="set HARNESS_SMOKE_MODEL_A for real perf")
@pytest.mark.asyncio
async def test_pf02_real_simple_task():
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        start = time.perf_counter()
        out = await run_tool_loop(
            model,
            [
                {
                    "role": "user",
                    "content": "Call echo once with text=ORION-42, then answer with the result.",
                }
            ],
            _echo_registry(),
            max_rounds=3,
        )
        elapsed = time.perf_counter() - start
    finally:
        reset_request_model(token)

    assert out["tool_trace"], out["text"]
    assert elapsed < 8.0, f"simple task {elapsed:.3f}s >= 8s"


@pytest.mark.asyncio
async def test_pf05_token_budget_fake():
    class _UsageModel:
        tier = "fake"

        async def ainvoke(self, messages, *, tools=None, **kwargs):
            return ChatResult(
                content="ok",
                finish_reason="stop",
                usage={"input": 1000, "output": 500, "total": 1500},
            )

    model = _UsageModel()
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _echo_registry(),
        adapter=ToolAdapter(model, mode="native"),
    )
    assert out["usage"]["total"] < 30_000


@pytest.mark.asyncio
async def test_pf06_cost_budget_fake():
    class _UsageModel:
        tier = "fake"

        async def ainvoke(self, messages, *, tools=None, **kwargs):
            return ChatResult(
                content="ok",
                finish_reason="stop",
                usage={"input": 10, "output": 10, "total": 20},
            )

    model = _UsageModel()
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _echo_registry(),
        adapter=ToolAdapter(model, mode="native"),
    )
    assert out["usage"]["cost"] <= 0.30


@pytest.mark.asyncio
async def test_pf07_switch_latency_fake(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from harness.switch import SwitchDeps, SwitchService

    messages = [HumanMessage(content="small", id=f"m{i}") for i in range(20)]
    target = (os.getenv("HARNESS_SMOKE_MODEL_B") or _MODEL).strip()
    sess = SimpleNamespace(model=_MODEL, status="idle", updated_at=0.0)
    deps = SwitchDeps(
        get_session=lambda _sid: sess,
        persist_session=lambda _sid: None,
        read_messages=lambda _sid: _msgs(messages),
        graph_state=lambda _sid: _empty(),
        context_binding=lambda _sid: {},
        read_limits=lambda _mid: {"context_length": 128000},
        get_epoch=lambda _sid: None,
        upsert_epoch=lambda *args: None,
        stable_message_id=lambda _sid, m: getattr(m, "id", ""),
        backup_dir=str(tmp_path / "switches"),
    )
    start = time.perf_counter()
    await SwitchService(deps).switch("s1", "model/b")
    assert time.perf_counter() - start < 15.0


async def _msgs(messages):
    return messages


async def _empty():
    return {}


@pytest.mark.skipif(not _MODEL, reason="set HARNESS_SMOKE_MODEL_A for real perf")
@pytest.mark.asyncio
async def test_pf05_pf06_real_token_and_cost():
    model = TierModel(tier="auto", timeout=120)
    token = set_request_model(_MODEL)
    try:
        out = await run_tool_loop(
            model,
            [{"role": "user", "content": "Reply with OK."}],
            _echo_registry(),
            max_rounds=1,
        )
    finally:
        reset_request_model(token)

    assert out["usage"]["total"] < 30_000
    assert out["usage"]["cost"] <= 0.30


@pytest.mark.skipif(not _MODEL, reason="set HARNESS_SMOKE_MODEL_A for real perf")
@pytest.mark.asyncio
async def test_pf07_real_switch_latency(tmp_path):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from harness.switch import SwitchDeps, SwitchService

    messages = []
    for i in range(30):
        messages.append(HumanMessage(content=("filler " * 120) + f" step {i} /work/app.py", id=f"m{i}"))
    target = (os.getenv("HARNESS_SMOKE_MODEL_B") or _MODEL).strip()
    sess = SimpleNamespace(model=_MODEL, status="idle", updated_at=0.0)

    async def read_messages(_sid):
        return messages

    async def graph_state(_sid):
        return {}

    deps = SwitchDeps(
        get_session=lambda _sid: sess,
        persist_session=lambda _sid: None,
        read_messages=read_messages,
        graph_state=graph_state,
        context_binding=lambda _sid: {},
        read_limits=lambda _mid: {"context_length": 8000},
        get_epoch=lambda _sid: None,
        upsert_epoch=lambda *args: None,
        stable_message_id=lambda _sid, m: getattr(m, "id", ""),
        backup_dir=str(tmp_path / "switches"),
        set_request_model=set_request_model,
        reset_request_model=reset_request_model,
    )
    start = time.perf_counter()
    out = await SwitchService(deps).switch("s1", target)
    elapsed = time.perf_counter() - start
    assert out["switched"] is True
    assert elapsed < 15.0
