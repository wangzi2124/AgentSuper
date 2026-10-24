"""Stable Harness contracts (design §11.4)."""

from __future__ import annotations

import json

from app.api.compat import _sse
from harness.adapter import apply_result
from harness.compression import CompressionResult
from harness.contracts import EventEnvelope, TurnContext, TurnResult
from harness.delimiters import render_tool_call
from harness.errors import classify_error
from harness.kernel import reset_current_turn, set_current_turn
from models.router import ChatResult


def test_turn_contracts_have_expected_fields():
    turn = TurnContext(turn_id="turn_1", session_id="ses_1", model="model/a")
    result = TurnResult(text="ok", rounds=2, usage={"cost": 0.1})
    event = EventEnvelope(type="tool_start", turn_id="turn_1", session_id="ses_1")

    assert turn.session_id == "ses_1"
    assert result.finish_reason == "stop"
    assert event.to_dict()["turn_id"] == "turn_1"


def test_sse_event_inherits_turn_context():
    turn = TurnContext(turn_id="turn_sse", session_id="ses_sse")
    token = set_current_turn(turn)
    try:
        line = _sse({"type": "routing"})
    finally:
        reset_current_turn(token)

    payload = json.loads(line.removeprefix("data: ").strip())
    assert payload["turn_id"] == "turn_sse"
    assert payload["session_id"] == "ses_sse"


def test_tool_adapter_contract_is_vendor_neutral():
    result = apply_result(
        ChatResult(content=render_tool_call("echo", {"text": "hi"})),
        "fallback",
    )
    call = result.tool_calls[0]
    assert call.name == "echo"
    assert json.loads(call.arguments) == {"text": "hi"}
    assert call.id


def test_error_classification_exposes_retry_contract():
    class RateLimited(Exception):
        status_code = 429

    err = classify_error(RateLimited("slow down"))
    assert err.retryable is True
    assert err.status_code == 429
    assert err.error_type == "rate_limit"
    assert set(err.to_dict()) == {
        "code",
        "message",
        "retryable",
        "status_code",
        "error_type",
    }


def test_compression_result_contract():
    result = CompressionResult(summary="s", triggered=True, tokens=100)
    assert result.recent == []
    assert result.missing_precision == []
    assert result.compress_count == 0
