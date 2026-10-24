from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from harness.compression import (
    CompressionError,
    CompressionPlan,
    compress_graph_messages,
    compress_messages,
    summarize_messages,
    tail_snapshot,
)


class _FakeModel:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.text)


def test_plan_for_window_is_bounded_and_paired():
    plan = CompressionPlan.for_window(10_000)
    assert plan.threshold == 6_000
    assert plan.keep_tokens == 2_500

    fallback = CompressionPlan.for_window(0)
    assert fallback.threshold == 80_000
    assert fallback.keep_tokens == 20_000


@pytest.mark.asyncio
async def test_compress_messages_not_triggered_keeps_history():
    messages = [HumanMessage(content="hi", id="m1")]
    out = await compress_messages(messages, threshold=100, keep_tokens=10)
    assert out.triggered is False
    assert out.summary == ""
    assert out.recent == messages


@pytest.mark.asyncio
async def test_compress_messages_uses_canonical_compressor_and_backfills(monkeypatch):
    messages = [
        HumanMessage(content="read /work/app.py", id="m1"),
        AIMessage(content="done", id="m2"),
    ]

    async def fake_compress(msgs, **kwargs):
        assert kwargs["threshold"] == 5
        return {
            "summary": "project facts",
            "recent": [msgs[-1]],
            "triggered": True,
            "tokens": 500,
        }

    monkeypatch.setattr("cognition.compressor.compress_context", fake_compress)
    out = await compress_messages(
        messages, threshold=5, keep_tokens=10, require_precision=True
    )

    assert out.triggered is True
    assert "/work/app.py" in out.summary
    assert out.tail_start_id == "m2"
    assert out.missing_precision == []


@pytest.mark.asyncio
async def test_compress_messages_raises_when_precision_missing(monkeypatch):
    messages = [
        HumanMessage(content="read /work/app.py", id="m1"),
        AIMessage(content="done", id="m2"),
    ]

    async def fake_compress(msgs, **kwargs):
        return {
            "summary": "no facts",
            "recent": [msgs[-1]],
            "triggered": True,
            "tokens": 500,
        }

    monkeypatch.setattr("cognition.compressor.compress_context", fake_compress)
    monkeypatch.setattr(
        "cognition.compressor.enforce_precision", lambda summary, older: summary
    )
    monkeypatch.setattr(
        "cognition.compressor.extract_precision_values",
        lambda text: ["/work/app.py"],
    )

    with pytest.raises(CompressionError):
        await compress_messages(
            messages, threshold=5, keep_tokens=10, require_precision=True
        )


@pytest.mark.asyncio
async def test_summarize_messages_uses_existing_summary_and_model():
    model = _FakeModel("new summary")
    out = await summarize_messages(
        [HumanMessage(content="old", id="m1")], "existing", model
    )
    assert out == "new summary"
    assert model.calls


@pytest.mark.asyncio
async def test_graph_compress_gate_true_uses_model_and_increments_count(monkeypatch):
    messages = [
        HumanMessage(content=f"msg {i} /work/app.py", id=f"m{i}")
        for i in range(8)
    ]
    model = _FakeModel("graph summary")
    monkeypatch.setattr(
        "cognition.compressor.should_use_llm_summary",
        lambda older, compress_count=0: True,
    )

    out = await compress_graph_messages(
        messages,
        existing_summary="old",
        model=model,
        compress_count=2,
        keep_recent=2,
    )

    assert out.triggered is True
    assert out.summary.startswith("graph summary")
    assert "/work/app.py" in out.summary
    assert out.compress_count == 3
    assert out.recent == messages[-2:]
    assert out.tail_start_id == "m6"


@pytest.mark.asyncio
async def test_graph_compress_gate_false_backfills_without_model(monkeypatch):
    messages = [
        HumanMessage(content="read /work/app.py", id="m1"),
        AIMessage(content="done", id="m2"),
        HumanMessage(content="tail", id="m3"),
    ]
    monkeypatch.setattr(
        "cognition.compressor.should_use_llm_summary",
        lambda older, compress_count=0: False,
    )

    out = await compress_graph_messages(
        messages,
        existing_summary="old",
        model=None,
        compress_count=1,
        keep_recent=1,
    )

    assert out.triggered is True
    assert out.compress_count == 1
    assert "/work/app.py" in out.summary
    assert out.recent == messages[-1:]


def test_tail_snapshot_reads_id_and_seq():
    out = tail_snapshot([SimpleNamespace(id="m1", seq=4)])
    assert out == {"tail_start_id": "m1", "tail_start_seq": 4}
    assert tail_snapshot([]) == {}
