from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import harness.compression as compression
from harness.compression import (
    CompactionPolicy,
    CompressionError,
    CompressionPlan,
    ContextCompactor,
    compress_messages,
    parse_buffer,
    select_tail_messages,
    summarize_messages,
    tail_snapshot,
    tail_start_id,
    turn_ranges,
)


class _FakeModel:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.text)


def test_plan_for_window_is_bounded_and_paired():
    # reserved = min(20k, normalized output); preserve = clamp(25% × usable, 2k, 8k)
    plan = CompressionPlan.for_window(10_000)
    assert plan.threshold == 7_500
    assert plan.keep_tokens == 2_000
    assert plan.tail_turns == 2

    sized = CompressionPlan.for_window(100_000, max_output_tokens=8_000)
    assert sized.threshold == 92_000
    assert sized.keep_tokens == 8_000

    fallback = CompressionPlan.for_window(0)
    assert fallback.threshold == 80_000
    assert fallback.keep_tokens == 15_000


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

    async def fake_summarize(msgs, existing_summary="", model=None, **kwargs):
        return "project facts"

    monkeypatch.setattr("harness.compression.summarize_messages", fake_summarize)
    out = await compress_messages(
        messages, threshold=5, keep_tokens=6, require_precision=True, require_llm=True
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

    async def fake_summarize(msgs, existing_summary="", model=None, **kwargs):
        return "no facts"

    monkeypatch.setattr("harness.compression.summarize_messages", fake_summarize)
    monkeypatch.setattr(
        "cognition.compressor.enforce_precision", lambda summary, older: summary
    )
    monkeypatch.setattr(
        "cognition.compressor.extract_precision_values",
        lambda text: ["/work/app.py"],
    )

    with pytest.raises(CompressionError):
        await compress_messages(
            messages, threshold=5, keep_tokens=6, require_precision=True, require_llm=True
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

    out = await compress_messages(
        messages,
        existing_summary="old",
        model=model,
        threshold=0,
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
    out = await compress_messages(
        messages,
        existing_summary="old",
        model=None,
        threshold=0,
        keep_tokens=0,
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
    assert tail_start_id([]) is None


def test_parse_buffer_handles_percent_absolute_and_invalid():
    assert parse_buffer("50%", 100_000) == 50_000
    assert parse_buffer("bad%", 100_000) == 16_000
    assert parse_buffer("20000", 100_000) == 20_000
    assert parse_buffer("bad", 100_000) == 16_000
    assert parse_buffer(None, 0) == 16_000

    class _Boom:
        def __str__(self):
            raise RuntimeError("boom")

    assert parse_buffer(_Boom(), 100_000) == 16_000


def test_defaults_fall_back_when_config_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "config", None)
    assert compression._keep_default(None) == 0
    assert compression._buffer_default(100_000) == 16_000


def test_tail_selection_edge_cases():
    assert turn_ranges([AIMessage(content="x")]) == []
    assert select_tail_messages([], 10, 0) == ([], [])
    only_ai = [AIMessage(content="x")]
    assert select_tail_messages(only_ai, 10, 0) == (only_ai, [])
    # tail_turns <= 0 表示不保留 tail（对齐 opencode），全部进入 head。
    only_user = [HumanMessage(content="")]
    assert select_tail_messages(only_user, 0, 0) == (only_user, [])


@pytest.mark.asyncio
async def test_summarize_empty_and_default_model(monkeypatch):
    assert await summarize_messages([], "existing") == "existing"

    model = _FakeModel("auto summary")

    def _get_model(_tier):
        return model

    monkeypatch.setattr("models.router.get_model", _get_model)
    out = await summarize_messages([HumanMessage(content="hello", id="m1")])
    assert out == "auto summary"


@pytest.mark.asyncio
async def test_summarize_segments_and_falls_back_on_error(monkeypatch):
    model = _FakeModel("segmented")
    messages = [HumanMessage(content="one " * 20, id="m1"), AIMessage(content="two " * 20, id="m2")]
    out = await summarize_messages(messages, model=model, segment_tokens=1)
    assert out == "segmented"
    assert len(model.calls) >= 2

    class _Boom:
        async def ainvoke(self, _messages):
            raise RuntimeError("down")

    out = await summarize_messages(messages, model=_Boom())
    assert out  # deterministic fallback


@pytest.mark.asyncio
async def test_compress_messages_noop_paths_and_fallback_summary():
    keep_all = [HumanMessage(content="hi")]
    out = await compress_messages(keep_all, threshold=-1, keep_tokens=100)
    assert out.triggered is False

    messages = [HumanMessage(content="fact /work/app.py"), AIMessage(content="done")]
    out = await compress_messages(messages, threshold=0, keep_tokens=0)
    assert out.triggered is True
    assert out.summary


@pytest.mark.asyncio
async def test_graph_compress_turn_mode_and_noop():
    messages = [
        HumanMessage(content="a /work/app.py", id="m1"),
        AIMessage(content="b", id="m2"),
    ]
    out = await compress_messages(
        messages, threshold=0, keep_tokens=1, keep_recent=1, model=None
    )
    assert out.triggered is True

    no_op = await compress_messages(
        [AIMessage(content="x")], threshold=0, keep_tokens=0, keep_recent=1
    )
    assert no_op.triggered is False


def test_auto_policy_disables_second_gate_and_sizes_legacy_tail():
    legacy = CompactionPolicy.for_auto(
        usable=10_000, keep_recent=6, keep_tokens=0, legacy=True
    )
    assert legacy.threshold == 0
    assert legacy.keep_tokens == 0
    assert legacy.min_gain_tokens == 0
    assert legacy.keep_recent == 6

    turn_based = CompactionPolicy.for_auto(
        usable=10_000, keep_recent=0, keep_tokens=0, legacy=False
    )
    assert turn_based.threshold == 0
    assert turn_based.keep_tokens == 2_500
    assert turn_based.min_gain_tokens == 1_024
    assert turn_based.keep_recent == 0


@pytest.mark.asyncio
async def test_compactor_auto_legacy_keeps_recent_messages():
    messages = [HumanMessage(content=f"m{i}", id=f"m{i}") for i in range(4)]
    out = await ContextCompactor().compress(
        messages,
        policy=CompactionPolicy.for_auto(
            usable=10_000, keep_recent=2, legacy=True
        ),
    )
    assert out.triggered is True
    assert out.recent == messages[-2:]
    assert out.compress_count == 0
