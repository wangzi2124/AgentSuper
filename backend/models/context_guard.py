"""Ollama prompt fitting guard.

The guard runs at the model transport boundary (``TierModel``) so every caller is
covered. It resolves the model's real context window, keeps an output reserve,
truncates old history when needed, and fails fast with an actionable error when
even the mandatory prefix (system + tool schemas + newest user message) cannot
fit.

Only Ollama is affected; cloud providers keep their existing behavior.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from cognition.token_counter import (
    estimate_tokens_messages,
    estimate_tools,
    sanitize_tool_messages,
    truncate_messages,
)
from config import settings

logger = logging.getLogger(__name__)

RESERVE_RATIO = 0.25
RESERVE_MIN = 512
SLACK_RATIO = 0.08
SLACK_MIN = 256
CALIBRATION_ALPHA = 0.3
CALIBRATION_MIN = 1.0
CALIBRATION_MAX = 2.0
AUTO_MAX_WINDOW = 32768

_OVERFLOW_HINTS = (
    "exceeds the available context size",
    "exceed_context_size_error",
    "context size",
    "n_ctx",
    "context length",
    "maximum context",
    "too many tokens",
)

_lock = threading.Lock()
_ratios: dict[str, float] = {}
_last_fit: dict[str, "FitResult"] = {}


@dataclass
class FitResult:
    """Outcome of fitting one prompt to the model's runtime window."""

    model: str
    messages: list[dict[str, Any]]
    window: int = 0
    prompt_budget: int = 0
    reserve: int = 0
    estimated_before: int = 0
    estimated_after: int = 0
    truncated: bool = False
    dropped_messages: int = 0
    num_ctx: int = 0


def guard_enabled() -> bool:
    return bool(getattr(settings, "ollama_context_guard", True))


def estimate_ratio(model_id: str) -> float:
    mid = _mid(model_id)
    with _lock:
        return _ratios.get(mid, 1.0)


def record_actual_usage(model_id: str, estimated: int, actual: int) -> None:
    """Update the per-model estimate calibration from a real prompt token count."""
    mid = _mid(model_id)
    estimated = int(estimated or 0)
    actual = int(actual or 0)
    if not mid or estimated <= 0 or actual <= 0:
        return
    ratio = actual / estimated
    ratio = max(CALIBRATION_MIN, min(CALIBRATION_MAX, ratio))
    with _lock:
        previous = _ratios.get(mid)
        if previous is None:
            _ratios[mid] = ratio
        else:
            _ratios[mid] = (
                previous * (1.0 - CALIBRATION_ALPHA) + ratio * CALIBRATION_ALPHA
            )


def resolve_window(model_id: str) -> int:
    """Real window: explicit override > /api/ps (last-known) > OLLAMA_NUM_CTX."""
    try:
        from models.catalog import effective_context_length

        return max(
            0, int(effective_context_length(model_id, settings.ollama_num_ctx) or 0)
        )
    except Exception:  # noqa: BLE001 - window probing must never block a call
        logger.debug("resolve_window failed for %s", model_id, exc_info=True)
        return 0


def split_budget(window: int) -> tuple[int, int]:
    """Return ``(output_reserve, prompt_budget)`` for a window."""
    window = max(0, int(window or 0))
    reserve = max(RESERVE_MIN, int(window * RESERVE_RATIO))
    slack = max(SLACK_MIN, int(window * SLACK_RATIO))
    return reserve, max(0, window - reserve - slack)


def minimum_window(required: int) -> int:
    """Smallest window whose prompt budget can hold ``required`` tokens."""
    required = max(0, int(required or 0))
    if required <= 0:
        return 0
    window = required
    for _ in range(32):
        reserve, _ = split_budget(window)
        slack = max(SLACK_MIN, int(window * SLACK_RATIO))
        needed = required + reserve + slack
        if needed <= window:
            return window
        window = needed
    return window


def fit_messages(
    model_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> FitResult:
    """Fit ``messages`` to the model window, or raise ``ContextOverflowError``."""
    mid = _mid(model_id)
    passthrough = FitResult(model=mid, messages=messages)
    if not messages or not guard_enabled() or not _is_ollama(mid):
        return passthrough

    window = resolve_window(mid)
    if window <= 0:
        return passthrough

    reserve, budget = split_budget(window)
    ratio = estimate_ratio(mid)
    before = _estimate(messages, tools, ratio)
    fit = FitResult(
        model=mid,
        messages=messages,
        window=window,
        prompt_budget=budget,
        reserve=reserve,
        estimated_before=before,
        estimated_after=before,
    )

    mandatory = _mandatory_tokens(messages, tools, ratio)
    if mandatory > budget:
        previous = window
        expanded = _auto_window(mandatory)
        if expanded <= 0:
            _raise_overflow(mid, window, budget, mandatory, reserve)
        window = expanded
        reserve, budget = split_budget(window)
        fit.window = window
        fit.reserve = reserve
        fit.prompt_budget = budget
        fit.num_ctx = window
        logger.info(
            "Ollama window auto-expanded: model=%s %d->%d tokens (required=%d)",
            mid,
            previous,
            window,
            mandatory,
        )
        if mandatory > budget:
            _raise_overflow(mid, window, budget, mandatory, reserve)

    if before <= budget:
        _last_fit[mid] = fit
        return fit

    fitted = truncate_messages(
        messages,
        max_tokens=budget,
        reserve_tokens=0,
        tool_defs=tools,
    )
    fitted = sanitize_tool_messages(fitted)
    after = _estimate(fitted, tools, ratio)

    fit.messages = fitted
    fit.estimated_after = after
    fit.truncated = True
    fit.dropped_messages = max(0, len(messages) - len(fitted))

    if after > budget:
        _raise_overflow(mid, window, budget, after, reserve)

    _last_fit[mid] = fit
    _emit_truncated(fit)
    logger.info(
        "Ollama prompt fitted: model=%s window=%d budget=%d tokens %d->%d dropped=%d",
        mid,
        window,
        budget,
        before,
        after,
        fit.dropped_messages,
    )
    return fit


def refit_after_overflow(
    model_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> FitResult:
    """Drop stale runtime-window cache, re-probe, and fit again (one retry)."""
    mid = _mid(model_id)
    try:
        from models.catalog import forget_runtime_context

        forget_runtime_context(mid, reprobe=True)
    except Exception:  # noqa: BLE001 - cache invalidation is best-effort
        logger.debug("forget_runtime_context failed for %s", mid, exc_info=True)
    return fit_messages(mid, messages, tools)


def is_overflow_error(exc: BaseException) -> bool:
    if str(getattr(exc, "error_type", "") or "") == "context_overflow":
        return True
    if str(getattr(exc, "code", "") or "").upper() == "CONTEXT_OVERFLOW":
        return True
    text = str(exc or "").lower()
    if any(hint in text for hint in _OVERFLOW_HINTS):
        return True
    if "上下文窗口" in text or "超出窗口" in text:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        return int(status) == 400 and "context" in text
    except (TypeError, ValueError):
        return False


def last_fit(model_id: str) -> FitResult | None:
    mid = _mid(model_id)
    with _lock:
        return _last_fit.get(mid)


def reset_state() -> None:
    """Test helper: clear calibration and last-fit caches."""
    with _lock:
        _ratios.clear()
        _last_fit.clear()


def _mid(model_id: str) -> str:
    raw = str(model_id or "")
    try:
        from models.catalog import normalize_model

        return normalize_model(raw) or raw
    except Exception:  # noqa: BLE001
        return raw


def _is_ollama(model_id: str) -> bool:
    try:
        from models.catalog import is_ollama_model

        return is_ollama_model(model_id)
    except Exception:  # noqa: BLE001
        return str(model_id or "").lower().startswith(("ollama/", "ollama_chat/"))


def _scale(value: int, ratio: float) -> int:
    return max(0, int(int(value or 0) * float(ratio or 1.0)))


def _estimate(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    ratio: float,
) -> int:
    raw = int(estimate_tokens_messages(messages)) + int(estimate_tools(tools))
    return _scale(raw, ratio)


def _mandatory_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    ratio: float,
) -> int:
    system = next((m for m in messages if m.get("role") == "system"), None)
    user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    raw = 0
    if system is not None:
        raw += int(estimate_tokens_messages([system]))
    if user is not None:
        raw += int(estimate_tokens_messages([user]))
    raw += int(estimate_tools(tools))
    return _scale(raw, ratio)


def _round_up_pow2(value: int) -> int:
    value = max(1, int(value or 0))
    return 1 << max(12, (value - 1).bit_length())


def _recommended_window(required: int) -> int:
    """Smallest power-of-two window that fits ``required`` tokens."""
    return _round_up_pow2(minimum_window(required))


def _auto_window(required: int) -> int:
    """Window to request from Ollama automatically, or 0 when impossible."""
    desired = _recommended_window(required)
    if AUTO_MAX_WINDOW > 0:
        desired = min(desired, AUTO_MAX_WINDOW)
    if desired <= 0:
        return 0
    _, budget = split_budget(desired)
    return desired if budget >= required else 0


def _raise_overflow(
    model_id: str,
    window: int,
    budget: int,
    required: int,
    reserve: int,
) -> None:
    from harness.errors import ContextOverflowError

    suggested = _recommended_window(required)
    if AUTO_MAX_WINDOW and suggested > AUTO_MAX_WINDOW:
        advice = (
            f"自动扩展上限 {AUTO_MAX_WINDOW} tokens 仍不足，需 {suggested}；"
            "请减少工具/Skill、system 提示或当前输入。"
        )
    else:
        advice = (
            f"请把该模型上下文窗口提高到 ≥{suggested}："
            "在「模型管理」设置 context_length（会通过 num_ctx 下发），"
            "或设置 OLLAMA_CONTEXT_LENGTH 后重启 Ollama；也可减少工具/Skill 或输入。"
        )
    exc = ContextOverflowError(
        f"Ollama 上下文窗口不足：模型 {model_id} 当前窗口 {window} tokens，"
        f"system+工具+当前消息至少需要 {required}，预留输出 {reserve} 后预算仅 {budget}。"
        f"{advice}"
    )
    exc.preflight = True  # 本地预检失败：重试没有意义，直接上抛
    raise exc


def _emit_truncated(fit: FitResult) -> None:
    try:
        from graph.events import push_event

        push_event(
            {
                "type": "context_truncated",
                "agent_id": "build",
                "agent_name": "build",
                "model": fit.model,
                "window": fit.window,
                "budget": fit.prompt_budget,
                "reserve": fit.reserve,
                "estimated_before": fit.estimated_before,
                "estimated_after": fit.estimated_after,
                "dropped_messages": fit.dropped_messages,
            }
        )
    except Exception:  # noqa: BLE001 - observability must never break a turn
        logger.debug("context_truncated event failed", exc_info=True)


__all__ = [
    "FitResult",
    "estimate_ratio",
    "fit_messages",
    "guard_enabled",
    "is_overflow_error",
    "last_fit",
    "minimum_window",
    "record_actual_usage",
    "refit_after_overflow",
    "reset_state",
    "resolve_window",
    "split_budget",
]
