"""单元测试：Ollama prompt 适配守卫（窗口/预算/截断/校准/失败/重试）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config import settings
from models import context_guard, router
from models.context_guard import FitResult
from models.router import TierModel


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    context_guard.reset_state()
    monkeypatch.setattr(settings, "ollama_context_guard", True)
    yield
    context_guard.reset_state()


def _patch_window(monkeypatch, window):
    monkeypatch.setattr(context_guard, "resolve_window", lambda mid: window)


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_split_budget_reserves_output_and_slack():
    assert context_guard.split_budget(4096) == (1024, 2745)
    assert context_guard.split_budget(8192) == (2048, 5489)
    assert context_guard.split_budget(0) == (512, 0)


def test_non_ollama_passthrough(monkeypatch):
    _patch_window(monkeypatch, 4096)
    messages = [_msg("system", "x" * 100_000), _msg("user", "hi")]
    fit = context_guard.fit_messages("deepseek/deepseek-v4-flash", messages)
    assert fit.window == 0
    assert fit.messages == messages
    assert fit.truncated is False


def test_guard_disabled_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "ollama_context_guard", False)
    _patch_window(monkeypatch, 4096)
    messages = [_msg("system", "x" * 100_000), _msg("user", "hi")]
    fit = context_guard.fit_messages("ollama_chat/x", messages)
    assert fit.messages == messages
    assert fit.window == 0


def test_truncates_history_but_keeps_system_and_latest_user(monkeypatch):
    _patch_window(monkeypatch, 4096)
    context_guard.reset_state()
    messages = [_msg("system", "sys")]
    for i in range(40):
        messages.append(_msg("user", f"question {i} " + "u" * 1200))
        messages.append(_msg("assistant", f"answer {i} " + "a" * 1200))
    messages.append(_msg("user", "LATEST REQUEST"))

    fit = context_guard.fit_messages("ollama_chat/x", messages)
    assert fit.truncated is True
    assert fit.estimated_after <= fit.prompt_budget
    assert fit.estimated_before > fit.estimated_after
    assert fit.dropped_messages > 0
    assert fit.messages[0] == messages[0]
    assert fit.messages[-1] == messages[-1]
    assert any("[earlier messages truncated" in str(m.get("content")) for m in fit.messages)


def test_mandatory_prefix_overflow_fails_fast(monkeypatch):
    _patch_window(monkeypatch, 4096)
    messages = [_msg("system", "s" * 200_000), _msg("user", "hi")]
    with pytest.raises(Exception) as excinfo:
        context_guard.fit_messages("ollama_chat/x", messages)
    exc = excinfo.value
    assert exc.code == "CONTEXT_OVERFLOW"
    assert "自动扩展上限" in str(exc)
    assert context_guard.is_overflow_error(exc)


def test_minimum_window_is_smallest_fitting_window():
    for required in (1, 1000, 2745, 5489, 8290):
        window = context_guard.minimum_window(required)
        assert context_guard.split_budget(window)[1] >= required
        assert window == required or context_guard.split_budget(window - 1)[1] < required


def test_overflow_message_recommends_a_window_that_fits():
    with pytest.raises(Exception) as excinfo:
        context_guard._raise_overflow("ollama_chat/x", 4096, 2745, 8290, 1024)
    message = str(excinfo.value)
    assert "≥16384" in message
    assert context_guard.split_budget(16384)[1] >= 8290


def test_tool_schemas_count_toward_mandatory_prefix_and_auto_expand(monkeypatch):
    _patch_window(monkeypatch, 4096)
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "d" * 2000,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for i in range(8)
    ]
    fit = context_guard.fit_messages(
        "ollama_chat/x", [_msg("system", "s"), _msg("user", "hi")], tools
    )
    assert fit.window == 8192
    assert fit.num_ctx == 8192
    assert fit.prompt_budget >= context_guard._mandatory_tokens(
        [_msg("system", "s"), _msg("user", "hi")], tools, 1.0
    )


def test_mandatory_overflow_auto_expands_window(monkeypatch):
    _patch_window(monkeypatch, 4096)
    monkeypatch.setattr(context_guard, "_mandatory_tokens", lambda *a, **k: 8290)
    fit = context_guard.fit_messages(
        "ollama_chat/x", [_msg("system", "s"), _msg("user", "hi")]
    )
    assert fit.window == 16384
    assert fit.num_ctx == 16384
    assert fit.prompt_budget == context_guard.split_budget(16384)[1]


def test_calibration_ewma_and_clamp():
    context_guard.record_actual_usage("ollama_chat/x", 1000, 1500)
    assert context_guard.estimate_ratio("ollama_chat/x") == pytest.approx(1.5)
    context_guard.record_actual_usage("ollama_chat/x", 1000, 3000)
    # ratio 3.0 clamps to 2.0; EWMA: 1.5*0.7 + 2.0*0.3
    assert context_guard.estimate_ratio("ollama_chat/x") == pytest.approx(1.65)
    context_guard.record_actual_usage("ollama_chat/x", 1000, 0)
    assert context_guard.estimate_ratio("ollama_chat/x") == pytest.approx(1.65)


def test_classify_error_recognizes_ollama_overflow():
    from harness.errors import classify_error

    err = classify_error(
        RuntimeError(
            "Ollama error: input length exceeds the available context size, n_ctx=4096"
        )
    )
    assert err.code == "CONTEXT_OVERFLOW"
    assert err.error_type == "context_overflow"
    assert err.retryable is False


def test_overflow_detection():
    assert context_guard.is_overflow_error(
        RuntimeError("This model's maximum context length is 4096 tokens")
    )
    assert context_guard.is_overflow_error(
        RuntimeError(
            "Ollama error: the input length exceeds the available context size, n_ctx=4096"
        )
    )
    assert not context_guard.is_overflow_error(RuntimeError("connection reset"))


def test_refit_invalidates_window_then_fits(monkeypatch):
    calls = {"n": 0}

    def fake_forget(mid, *, reprobe=True):
        calls["n"] += 1

    monkeypatch.setattr("models.catalog.forget_runtime_context", fake_forget)
    _patch_window(monkeypatch, 8192)
    fit = context_guard.refit_after_overflow(
        "ollama_chat/x", [_msg("system", "s"), _msg("user", "hi")]
    )
    assert calls["n"] == 1
    assert fit.window == 8192


def test_truncation_emits_event(monkeypatch):
    events = []
    _patch_window(monkeypatch, 4096)
    monkeypatch.setattr("graph.events.push_event", events.append)
    messages = [_msg("system", "sys")]
    for i in range(40):
        messages.append(_msg("user", f"q{i} " + "u" * 1200))
    context_guard.fit_messages("ollama_chat/x", messages)
    assert events and events[0]["type"] == "context_truncated"
    assert events[0]["window"] == 4096
    assert events[0]["dropped_messages"] > 0


def test_transport_uses_fit_output_and_reserve(monkeypatch):
    model = TierModel()
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {"model": "ollama_chat/x", "api_base": None, "api_key": "ollama"},
    )
    monkeypatch.setattr(
        router,
        "fit_messages",
        lambda mid, messages, tools=None: FitResult(
            model=mid,
            messages=[_msg("system", "fitted"), _msg("user", "hi")],
            window=4096,
            prompt_budget=2745,
            reserve=1024,
            estimated_before=5000,
            estimated_after=2000,
            truncated=True,
            dropped_messages=3,
            num_ctx=16384,
        ),
    )
    kwargs, _, fit = model._transport_kwargs([_msg("system", "x"), _msg("user", "hi")], None)
    assert kwargs["messages"] == [_msg("system", "fitted"), _msg("user", "hi")]
    assert kwargs["max_tokens"] == 1024
    assert kwargs["num_ctx"] == 16384
    assert fit.truncated is True


def _fake_response(content: str = "ok", prompt_tokens: int = 123):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=[]),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=2, total_tokens=prompt_tokens + 2
        ),
        model="ollama_chat/x",
    )


def test_raw_invoke_retries_once_on_overflow_and_calibrates(monkeypatch):
    model = TierModel()
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {"model": "ollama_chat/x", "api_base": None, "api_key": "ollama"},
    )
    fit = FitResult(
        model="ollama_chat/x",
        messages=[_msg("system", "s"), _msg("user", "hi")],
        window=4096,
        prompt_budget=2745,
        reserve=1024,
        estimated_before=1000,
        estimated_after=1000,
    )
    monkeypatch.setattr(router, "fit_messages", lambda *a, **k: fit)
    refits = []
    monkeypatch.setattr(
        router,
        "refit_after_overflow",
        lambda mid, messages, tools=None: refits.append(mid) or fit,
    )
    seen = []

    def fake_completion(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise RuntimeError(
                "exceeds the available context size, try increasing it (n_ctx=4096)"
            )
        return _fake_response(prompt_tokens=900)

    monkeypatch.setattr(router.litellm, "completion", fake_completion)
    captured = []
    monkeypatch.setattr(
        router, "record_actual_usage", lambda mid, est, actual: captured.append((est, actual))
    )

    result = model.raw_invoke([_msg("system", "s"), _msg("user", "hi")])
    assert result.content == "ok"
    assert len(seen) == 2
    assert refits == ["ollama_chat/x"]
    assert captured == [(1000, 900)]


def test_raw_stream_retries_once_on_overflow(monkeypatch):
    import asyncio

    from models.stream import StreamResult

    model = TierModel()
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {"model": "ollama_chat/x", "api_base": None, "api_key": "ollama"},
    )
    fit = FitResult(
        model="ollama_chat/x",
        messages=[_msg("system", "s"), _msg("user", "hi")],
        window=4096,
        prompt_budget=2745,
        reserve=1024,
        estimated_before=1000,
        estimated_after=1000,
        num_ctx=16384,
    )
    monkeypatch.setattr(router, "fit_messages", lambda *a, **k: fit)
    refits = []
    monkeypatch.setattr(
        router,
        "refit_after_overflow",
        lambda mid, messages, tools=None: refits.append(mid) or fit,
    )
    calls = {"n": 0}
    seen = []

    async def fake_stream(*args, **kwargs):
        calls["n"] += 1
        seen.append(kwargs)
        if calls["n"] == 1:
            raise RuntimeError("exceeds the available context size (n_ctx=4096)")
        return StreamResult(
            content="streamed",
            usage=SimpleNamespace(prompt_tokens=900),
        )

    monkeypatch.setattr("models.stream.stream_chat", fake_stream)
    captured = []
    monkeypatch.setattr(
        router, "record_actual_usage", lambda mid, est, actual: captured.append((est, actual))
    )

    result = asyncio.run(model.raw_stream([_msg("system", "s"), _msg("user", "hi")]))
    assert result.content == "streamed"
    assert calls["n"] == 2
    assert refits == ["ollama_chat/x"]
    assert captured == [(1000, 900)]
    assert seen[0]["extra"]["num_ctx"] == 16384


def test_preflight_overflow_is_not_retried(monkeypatch):
    from harness.errors import ContextOverflowError

    model = TierModel()
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {"model": "ollama_chat/x", "api_base": None, "api_key": "ollama"},
    )
    exc = ContextOverflowError("Ollama 上下文窗口不足")
    exc.preflight = True
    monkeypatch.setattr(
        router, "fit_messages", lambda *a, **k: (_ for _ in ()).throw(exc)
    )
    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        return _fake_response()

    monkeypatch.setattr(router.litellm, "completion", fake_completion)
    with pytest.raises(ContextOverflowError):
        model.raw_invoke([_msg("system", "s"), _msg("user", "hi")])
    assert calls["n"] == 0


def test_raw_invoke_does_not_retry_unrelated_errors(monkeypatch):
    model = TierModel()
    monkeypatch.setattr(
        model,
        "_endpoint",
        lambda: {"model": "ollama_chat/x", "api_base": None, "api_key": "ollama"},
    )
    monkeypatch.setattr(
        router,
        "fit_messages",
        lambda *a, **k: FitResult(
            model="ollama_chat/x",
            messages=[_msg("system", "s"), _msg("user", "hi")],
            window=4096,
            reserve=1024,
            prompt_budget=2745,
        ),
    )
    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        raise RuntimeError("connection reset")

    monkeypatch.setattr(router.litellm, "completion", fake_completion)
    with pytest.raises(RuntimeError, match="connection reset"):
        model.raw_invoke([_msg("system", "s"), _msg("user", "hi")])
    assert calls["n"] == 1
