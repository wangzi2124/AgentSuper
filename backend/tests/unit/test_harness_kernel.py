from __future__ import annotations

import asyncio

import pytest

from graph.events import push_event, reset_event_queue, set_event_queue
from harness.contracts import EventEnvelope, TurnContext, TurnResult
from harness.errors import (
    AuthError,
    BudgetExceededError,
    ContextOverflowError,
    RateLimitError,
    UpstreamError,
    classify_error,
)
from harness.kernel import TurnHarness, as_turn_result, current_turn


@pytest.mark.asyncio
async def test_turn_harness_sets_and_resets_contextvar():
    seen = {}

    async def runner(ctx: TurnContext, question: str, **kwargs):
        seen["ctx"] = ctx
        seen["question"] = question
        seen["inside"] = current_turn()
        return {"final_output": "ok", "token_usage": {"cost": 0.1}, "tool_rounds": 2}

    harness = TurnHarness(
        runner,
        id_factory=lambda sid: f"turn_{sid}_fixed",
        clock=lambda: 123.0,
    )
    raw = await harness.run(
        "hello",
        session_id="ses1",
        user_id="u1",
        model="model/a",
        directory="D:/work",
    )
    result = as_turn_result(raw)

    assert seen["inside"] is seen["ctx"]
    assert seen["ctx"].turn_id == "turn_ses1_fixed"
    assert seen["ctx"].started_at == 123.0
    assert seen["ctx"].model == "model/a"
    assert seen["question"] == "hello"
    assert current_turn() is None
    assert result.text == "ok"
    assert result.rounds == 2
    assert result.usage["cost"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_turn_harness_resets_contextvar_on_error():
    async def runner(ctx, question, **kwargs):
        raise RuntimeError("boom")

    harness = TurnHarness(runner)
    with pytest.raises(RuntimeError):
        await harness.run("q", session_id="ses2")
    assert current_turn() is None


@pytest.mark.asyncio
async def test_turn_harness_stream_sets_context_and_resets():
    seen = {}

    async def runner(ctx, question, **kwargs):
        seen["turn_id"] = ctx.turn_id
        seen["inside"] = current_turn()
        yield {"event": "start"}
        yield {"event": "done"}

    harness = TurnHarness(runner, id_factory=lambda sid: f"turn_{sid}_stream")
    items = [
        item
        async for item in harness.stream("q", session_id="ses3", user_id="u")
    ]

    assert items == [{"event": "start"}, {"event": "done"}]
    assert seen["turn_id"] == "turn_ses3_stream"
    assert seen["inside"] is not None
    assert current_turn() is None


@pytest.mark.asyncio
async def test_push_event_inherits_turn_id_from_kernel_context():
    async def runner(ctx, question, **kwargs):
        push_event({"type": "tool_start", "step_id": "s1"})
        return {"final_output": "ok"}

    queue: asyncio.Queue = asyncio.Queue()
    token = set_event_queue(queue)
    try:
        harness = TurnHarness(runner, id_factory=lambda sid: f"turn_{sid}_evt")
        await harness.run("q", session_id="ses4")
    finally:
        reset_event_queue(token)

    event = queue.get_nowait()
    assert event["turn_id"] == "turn_ses4_evt"
    assert event["session_id"] == "ses4"


def test_as_turn_result_accepts_turn_result_and_text():
    existing = TurnResult(text="x", rounds=1)
    assert as_turn_result(existing) is existing
    assert as_turn_result("plain").text == "plain"
    assert as_turn_result({"text": "from-text"}).text == "from-text"


def test_event_envelope_serializes_turn_fields():
    envelope = EventEnvelope(
        type="tool_start",
        turn_id="turn_1",
        session_id="ses1",
        agent_id="build",
        step_id="call_1",
        seq=2,
        ts=10.0,
        data={"name": "read_file"},
    )
    payload = envelope.to_dict()
    assert payload["turn_id"] == "turn_1"
    assert payload["step_id"] == "call_1"
    assert payload["data"]["name"] == "read_file"


def test_classify_error_maps_retry_and_status():
    assert isinstance(classify_error(AuthError()), AuthError)
    assert classify_error(RateLimitError()).retryable is True
    assert classify_error(UpstreamError()).status_code == 502
    assert isinstance(classify_error(TimeoutError("slow")), UpstreamError)
    assert isinstance(
        classify_error(RuntimeError("maximum context length exceeded")),
        ContextOverflowError,
    )
    assert isinstance(BudgetExceededError(), BudgetExceededError)
    assert classify_error(RuntimeError("misc")).code == "UNKNOWN"
