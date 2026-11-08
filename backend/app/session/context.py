"""Turn-scoped session context for platform tools (durable forms etc.)."""

from __future__ import annotations

import contextvars
from typing import Any

_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("current_session_id", default="")
_PLATFORM_CTX: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "current_platform_ctx", default=None
)


def set_session_context(session_id: str, ctx: Any = None) -> None:
    _SESSION_ID.set(str(session_id or ""))
    if ctx is not None:
        _PLATFORM_CTX.set(ctx)


def current_session_id() -> str:
    return _SESSION_ID.get()


def current_platform_ctx() -> Any:
    return _PLATFORM_CTX.get()


__all__ = ["current_platform_ctx", "current_session_id", "set_session_context"]
