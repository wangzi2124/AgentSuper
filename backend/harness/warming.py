"""Idle warming scheduler (opencode gap G-11).

Warming never enters the graph: it calls only the model runtime, discards every
response, and stops when the session has been active recently. Failures are
logged and counted; they never surface as chat errors.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

WarmFn = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class WarmingConfig:
    enabled: bool = False
    prompt: str = ""
    interval: float = 60.0
    duration: float = 0.0


def parse_config(raw: Any) -> WarmingConfig:
    if raw is True:
        return WarmingConfig(enabled=True)
    if not isinstance(raw, dict):
        return WarmingConfig()
    try:
        interval = float(raw.get("interval", 60) or 60)
    except (TypeError, ValueError):
        interval = 60.0
    try:
        duration = float(raw.get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return WarmingConfig(
        enabled=bool(raw.get("enabled", True)),
        prompt=str(raw.get("prompt") or ""),
        interval=max(1.0, interval),
        duration=max(0.0, duration),
    )


class WarmingScheduler:
    def __init__(
        self,
        warm: WarmFn,
        config: WarmingConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._warm = warm
        self.config = config
        self._clock = clock
        self.last_activity = clock()
        self.last_warm = float("-inf")
        self.warmed = 0
        self.failed = 0
        self._inflight = False

    def note_activity(self) -> None:
        self.last_activity = self._clock()

    def due(self, now: float | None = None) -> bool:
        if not self.config.enabled or self._inflight:
            return False
        moment = self._clock() if now is None else now
        idle = moment - self.last_activity
        if idle < self.config.interval:
            return False
        if self.config.duration > 0 and idle > self.config.duration:
            return False
        return moment - self.last_warm >= self.config.interval

    async def maybe_warm(self, now: float | None = None) -> bool:
        if not self.due(now):
            return False
        moment = self._clock() if now is None else now
        self._inflight = True
        self.last_warm = moment
        try:
            await self._warm(self.config.prompt)
            self.warmed += 1
            return True
        except Exception:  # noqa: BLE001 - warming must never break chat
            self.failed += 1
            logger.debug("idle warm failed", exc_info=True)
            return False
        finally:
            self._inflight = False

    async def run(self, stop: asyncio.Event, *, tick: float = 1.0) -> None:
        tick = max(0.1, float(tick))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick)
                return
            except asyncio.TimeoutError:
                await self.maybe_warm()

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "warmed": self.warmed,
            "failed": self.failed,
            "interval": self.config.interval,
            "duration": self.config.duration,
        }


def build_runtime_warmer(model_id: str) -> WarmFn:
    """Warm one local model through ModelRuntime; cloud models are no-ops."""

    async def _warm(prompt: str) -> Any:
        from models.runtime import runtime_for_model

        runtime = runtime_for_model(model_id)
        if not runtime.is_local():
            return None
        return await runtime.warm(prompt)

    return _warm


__all__ = [
    "WarmingConfig",
    "WarmingScheduler",
    "build_runtime_warmer",
    "parse_config",
]
