"""Harness error taxonomy: one place for retry/status/error_type decisions."""

from __future__ import annotations

from typing import Any


class HarnessError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "HARNESS_ERROR",
        retryable: bool = False,
        status_code: int = 500,
        error_type: str = "unknown",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.error_type = error_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "status_code": self.status_code,
            "error_type": self.error_type,
        }


class AuthError(HarnessError):
    def __init__(self, message: str = "模型鉴权失败") -> None:
        super().__init__(
            message, code="AUTH", retryable=False, status_code=401, error_type="auth"
        )


class RateLimitError(HarnessError):
    def __init__(self, message: str = "模型限流") -> None:
        super().__init__(
            message,
            code="RATE_LIMIT",
            retryable=True,
            status_code=429,
            error_type="rate_limit",
        )


class UpstreamError(HarnessError):
    def __init__(self, message: str = "模型上游失败") -> None:
        super().__init__(
            message,
            code="UPSTREAM",
            retryable=True,
            status_code=502,
            error_type="upstream",
        )


class ContextOverflowError(HarnessError):
    def __init__(self, message: str = "上下文超出模型窗口") -> None:
        super().__init__(
            message,
            code="CONTEXT_OVERFLOW",
            retryable=False,
            status_code=400,
            error_type="context_overflow",
        )


class BudgetExceededError(HarnessError):
    def __init__(self, message: str = "会话预算已用尽") -> None:
        super().__init__(
            message,
            code="BUDGET_EXCEEDED",
            retryable=False,
            status_code=429,
            error_type="budget_exceeded",
        )


_OVERFLOW_HINTS = (
    "context length",
    "maximum context",
    "context window",
    "context size",
    "exceeds the available context size",
    "exceed_context_size_error",
    "n_ctx",
    "try increasing it",
    "too many tokens",
    "上下文长度",
)


def classify_error(exc: BaseException) -> HarnessError:
    if isinstance(exc, HarnessError):
        return exc
    if type(exc).__name__ in ("BudgetExceeded", "BudgetExceededError"):
        return BudgetExceededError(str(exc))
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0
    text = str(exc).lower()
    if status in (401, 403):
        return AuthError(str(exc))
    if status == 429:
        return RateLimitError(str(exc))
    if status >= 500:
        return UpstreamError(str(exc))
    if isinstance(exc, TimeoutError):
        return UpstreamError(str(exc) or "模型调用超时")
    if any(hint in text for hint in _OVERFLOW_HINTS):
        return ContextOverflowError(str(exc))
    return HarnessError(
        str(exc) or exc.__class__.__name__,
        code="UNKNOWN",
        retryable=False,
        status_code=500,
        error_type="unknown",
    )


__all__ = [
    "AuthError",
    "BudgetExceededError",
    "ContextOverflowError",
    "HarnessError",
    "RateLimitError",
    "UpstreamError",
    "classify_error",
]
