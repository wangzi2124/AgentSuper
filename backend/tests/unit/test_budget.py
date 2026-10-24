from __future__ import annotations

import pytest

from graph.nodes.base import run_tool_loop
from harness.budget import BudgetExceeded, BudgetLedger
from models.router import ChatResult
from harness.tool_adapter import ToolAdapter


class _ScriptedModel:
    tier = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        return self.script.pop(0)


def test_budget_zero_means_unlimited():
    ledger = BudgetLedger(total=0, used=100)
    assert ledger.enabled is False
    assert ledger.remaining() == float("inf")
    assert ledger.exhausted() is False
    ledger.ensure_available()


def test_budget_charges_monotonically_and_exhausts():
    ledger = BudgetLedger(total=1.0, used=0.2)
    assert ledger.remaining() == pytest.approx(0.8)
    ledger.charge(0.3)
    assert ledger.used == pytest.approx(0.5)
    ledger.charge(0.5)
    assert ledger.exhausted() is True
    with pytest.raises(BudgetExceeded):
        ledger.ensure_available()


@pytest.mark.asyncio
async def test_exhausted_budget_stops_before_model_call():
    model = _ScriptedModel([])
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        registry=type("R", (), {"definitions": lambda self, scope=None: [], "get": lambda self, name: None})(),
        adapter=ToolAdapter(model, mode="native"),
        budget=BudgetLedger(total=1.0, used=1.0),
    )

    assert model.calls == 0
    assert out["budget_exceeded"] is True
    assert "预算" in out["text"]
    assert out["finish_reason"] == "budget-exceeded"


@pytest.mark.asyncio
async def test_budget_charges_each_model_call(monkeypatch):
    model = _ScriptedModel(
        [
            ChatResult(content="one", finish_reason="stop"),
            ChatResult(content="two", finish_reason="stop"),
        ]
    )
    ledger = BudgetLedger(total=1.0, used=0.0)

    monkeypatch.setattr(
        "graph.nodes.base.usage_from_result",
        lambda result, label=None: {
            "input": 1,
            "output": 1,
            "total": 2,
            "calls": 1,
            "cost": 0.6,
        },
    )

    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        registry=type("R", (), {"definitions": lambda self, scope=None: [], "get": lambda self, name: None})(),
        adapter=ToolAdapter(model, mode="native"),
        budget=ledger,
        max_rounds=1,
    )

    assert out["text"] == "one"
    assert ledger.used == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_exhausted_budget_emits_event():
    import asyncio
    from graph.events import reset_event_queue, set_event_queue

    model = _ScriptedModel([])
    q = asyncio.Queue()
    token = set_event_queue(q)
    try:
        out = await run_tool_loop(
            model,
            [{"role": "user", "content": "go"}],
            registry=type("R", (), {"definitions": lambda self, scope=None: [], "get": lambda self, name: None})(),
            adapter=ToolAdapter(model, mode="native"),
            budget=BudgetLedger(total=1.0, used=1.0),
        )
    finally:
        reset_event_queue(token)

    assert out["budget_exceeded"] is True
    assert q.get_nowait()["type"] == "budget_exceeded"
