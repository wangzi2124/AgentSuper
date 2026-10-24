"""Small retry helper for retryable Harness errors (429/5xx/timeout)."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from .errors import classify_error

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            err = classify_error(exc)
            if not err.retryable or attempt >= max_attempts:
                raise
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))


__all__ = ["retry_async"]
