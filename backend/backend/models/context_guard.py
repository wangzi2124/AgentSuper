"""Model prompt fitting guard.

The guard runs at the model transport boundary (``TierModel``) so every caller is
covered. It resolves the model's real context window, keeps an output reserve,
and raises ``ContextOverflowError`` when the prompt no longer fits. The caller
(the tool loop) then compacts with the current model instead of silently
truncating tool results. When the mandatory prefix (system + tool schemas +
newest user message) is the problem, the guard first tries to enlarge the
Ollama window automatically.

The check applies to every provider with a known context window; Ollama can
additionally grow its window via ``num_ctx`` before compaction is requested.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

RESERVE_RATIO = 0.25
RESERVE_MIN = 512
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
    from cognition.context_meter import get_meter

    return get_meter().ratio(_mid(model_id))


def record_actual_usage(model_id: str, estimated: int, actual: int) -> None:
    """Update the per-model estimate calibration from a real prompt token count."""
    from cognition.context_meter import get_meter

    get_meter().observe_model(_mid(model_id), estimated, actual)


def resolve_window(model_id: str) -> int:
    """Real window for any model: catalog override, runtime snapshot, fallback.

    Ollama falls back to ``OLLAMA_NUM_CTX``; other providers have no such
    process-level fallback, so an unknown window stays unknown (0) and the
    guard passes through rather than blocking the call.
    """
    try:
        from models.runtime import effective_window

        fallback = settings.ollama_num_ctx if _is_ollama(model_id) else 0
        return max(0, int(effective_window(model_id, fallback) or 0))
    except Exception:  # noqa: BLE001 - window resolution must never block a call
        logger.debug("resolve_window failed for %s", model_id, exc_info=True)
        return 0


def split_budget(window: int) -> tuple[int, int]:
    """Return ``(output_reserve, prompt_budget)`` for a window.

    Aligns with opencode: ``prompt_budget = window - output_reserve``. There is
    no extra percentage slack; estimation is calibrated by ``ContextMeter`` and
    a real provider overflow still falls back to compaction/retry.
    """
    window = max(0, int(window or 0))
    reserve = max(RESERVE_MIN, int(window * RESERVE_RATIO))
    return reserve, max(0, window - reserve)


def minimum_window(required: int) -> int:
    """Smallest window whose prompt budget can hold ``required`` tokens."""
    required = max(0, int(required or 0))
    if required <= 0:
        return 0
    window = required
    for _ in range(32):
        reserve, _ = split_budget(window)
        needed = required + reserve
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
    if not messages or not guard_enabled():
        return passthrough

    window = resolve_window(mid)
    if window <= 0:
        return passthrough

    reserve, budget = split_budget(window)
    before = _estimate(messages, tools, 1.0, mid)
    fit = FitResult(
        model=mid,
        messages=messages,
        window=window,
        prompt_budget=budget,
        reserve=reserve,
        estimated_before=before,
        estimated_after=before,
    )

    mandatory = _mandatory_tokens(messages, tools, 1.0, mid)
    if mandatory > budget:
        # Only a local Ollama call can be grown via num_ctx. For every other
        # provider the window is fixed, so overflow is handled by compaction.
        expanded = _auto_window(mandatory) if _is_ollama(mid) else 0
        if expanded <= 0:
            _raise_overflow(mid, window, budget, mandatory, reserve)
        previous = window
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

    if before > budget:
        # Do not truncate here: the tool loop owns compaction and can summarize
        # with the current model, preserving the latest tool round instead of
        # silently dropping it.
        logger.info(
            "Model prompt over budget: model=%s window=%d budget=%d tokens=%d; "
            "deferring to caller compaction",
            mid,
            window,
            budget,
            before,
        )
        _raise_overflow(mid, window, budget, before, reserve)

    _last_fit[mid] = fit
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
        _last_fit.clear()
    from cognition.context_meter import get_meter

    get_meter().reset_calibration()


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
    model_id: str = "",
) -> int:
    from cognition.context_meter import get_meter

    raw = get_meter().estimate(messages, tools=tools, model_id=model_id).total
    # With model_id, ContextMeter already applied the per-model ratio.
    return raw if model_id else _scale(raw, ratio)


def _mandatory_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    ratio: float,
    model_id: str = "",
) -> int:
    from cognition.context_meter import get_meter

    mandatory = [
        m
        for m in (
            next((m for m in messages if m.get("role") == "system"), None),
            next((m for m in reversed(messages) if m.get("role") == "user"), None),
        )
        if m is not None
    ]
    raw = get_meter().estimate(mandatory, tools=tools, model_id=model_id).total
    return raw if model_id else _scale(raw, ratio)


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
    if _is_ollama(model_id) and AUTO_MAX_WINDOW and suggested > AUTO_MAX_WINDOW:
        advice = (
            f"自动扩展上限 {AUTO_MAX_WINDOW} tokens 仍不足，需 {suggested}；"
            "请减少工具/Skill、system 提示或当前输入。"
        )
    elif _is_ollama(model_id):
        advice = (
            f"请把该模型上下文窗口提高到 ≥{suggested}："
            "在「模型管理」设置 context_length（会通过 num_ctx 下发），"
            "或设置 OLLAMA_CONTEXT_LENGTH 后重启 Ollama；也可减少工具/Skill 或输入。"
        )
    else:
        advice = (
            f"请在「模型管理」将该模型 context_length 设为 ≥{suggested}，"
            "或改用更大上下文的模型；也可减少工具/Skill、system 提示或当前输入。"
        )
    exc = ContextOverflowError(
        f"模型上下文窗口不足：模型 {model_id} 当前窗口 {window} tokens，"
        f"system+工具+当前消息至少需要 {required}，预留输出 {reserve} 后预算仅 {budget}。"
        f"{advice}"
    )
    exc.preflight = True  # 预检失败：重试没有意义，直接上抛让上层压缩
    raise exc


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
