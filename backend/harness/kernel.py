"""TurnHarness: one entry point for the turn lifecycle.

The kernel does not know how the graph runs; it creates a stable ``TurnContext``,
propagates it through a contextvar, and delegates to an injected runner. This keeps
budget/cancellation/events available to future call sites without rewriting the
current graph in one step.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable

from .contracts import TurnContext, TurnResult

Runner = Callable[..., Awaitable[Any]]
IdFactory = Callable[[str], str]

_current_turn: contextvars.ContextVar[TurnContext | None] = contextvars.ContextVar(
    "harness_turn", default=None
)


def current_turn() -> TurnContext | None:
    return _current_turn.get()


def set_current_turn(ctx: TurnContext) -> contextvars.Token:
    return _current_turn.set(ctx)


def reset_current_turn(token: contextvars.Token) -> None:
    _current_turn.reset(token)


def _default_turn_id(session_id: str) -> str:
    return f"turn_{session_id}_{uuid.uuid4().hex[:12]}"


class TurnHarness:
    def __init__(
        self,
        runner: Runner,
        *,
        id_factory: IdFactory | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.runner = runner
        self.id_factory = id_factory or _default_turn_id
        self.clock = clock

    async def run(
        self,
        question: str,
        *,
        session_id: str,
        user_id: str = "anonymous",
        model: str | None = None,
        directory: str | None = None,
        **kwargs: Any,
    ) -> Any:
        ctx = TurnContext(
            turn_id=self.id_factory(session_id),
            session_id=session_id,
            user_id=user_id,
            model=model,
            directory=directory,
            started_at=self.clock(),
        )
        token = _current_turn.set(ctx)
        runner_kwargs = dict(kwargs)
        runner_kwargs.setdefault("session_id", session_id)
        runner_kwargs.setdefault("user_id", user_id)
        try:
            return await self.runner(ctx, question, **runner_kwargs)
        finally:
            _current_turn.reset(token)

    async def stream(
        self,
        question: str,
        *,
        session_id: str,
        user_id: str = "anonymous",
        model: str | None = None,
        directory: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        ctx = TurnContext(
            turn_id=self.id_factory(session_id),
            session_id=session_id,
            user_id=user_id,
            model=model,
            directory=directory,
            started_at=self.clock(),
        )
        token = _current_turn.set(ctx)
        runner_kwargs = dict(kwargs)
        runner_kwargs.setdefault("session_id", session_id)
        runner_kwargs.setdefault("user_id", user_id)
        try:
            async for item in self.runner(ctx, question, **runner_kwargs):
                yield item
        finally:
            _current_turn.reset(token)


def as_turn_result(value: Any) -> TurnResult:
    """Best-effort conversion for runners returning graph state or a result."""
    if isinstance(value, TurnResult):
        return value
    if not isinstance(value, dict):
        return TurnResult(text=str(value or ""))
    return TurnResult(
        text=str(value.get("final_output") or value.get("text") or ""),
        usage=dict(value.get("token_usage") or value.get("usage") or {}),
        rounds=int(value.get("tool_rounds") or value.get("rounds") or 0),
        finish_reason=str(value.get("finish_reason") or "stop"),
        metadata={
            k: v
            for k, v in value.items()
            if k
            in (
                "budget_exceeded",
                "tool_history",
                "task_plan",
                "sub_results",
            )
        },
    )


__all__ = [
    "TurnHarness",
    "as_turn_result",
    "current_turn",
    "reset_current_turn",
    "set_current_turn",
]
