"""G-11: idle warming scheduler (fake clock)."""

from __future__ import annotations

import asyncio

from harness import warming


def test_interval_and_reset():
    calls: list[str] = []
    now = [100.0]

    async def warm(prompt: str) -> None:
        calls.append(prompt)

    scheduler = warming.WarmingScheduler(
        warm,
        warming.WarmingConfig(enabled=True, prompt="p", interval=10),
        clock=lambda: now[0],
    )
    assert scheduler.due(109) is False
    assert scheduler.due(110) is True
    assert asyncio.run(scheduler.maybe_warm(110)) is True
    assert calls == ["p"]
    assert scheduler.due(115) is False
    now[0] = 120
    assert scheduler.due() is True
    scheduler.note_activity()
    assert scheduler.due(125) is False


def test_duration_window():
    async def warm(prompt: str) -> None:
        return None

    scheduler = warming.WarmingScheduler(
        warm,
        warming.WarmingConfig(enabled=True, interval=10, duration=30),
        clock=lambda: 0.0,
    )
    assert scheduler.due(25) is True
    assert scheduler.due(31) is False


def test_failure_is_counted_not_raised():
    async def warm(prompt: str) -> None:
        raise RuntimeError("boom")

    scheduler = warming.WarmingScheduler(
        warm,
        warming.WarmingConfig(enabled=True, interval=1),
        clock=lambda: 10.0,
    )
    assert asyncio.run(scheduler.maybe_warm(11)) is False
    assert scheduler.failed == 1
    assert scheduler.stats()["warmed"] == 0


def test_disabled_never_warms():
    async def warm(prompt: str) -> None:  # pragma: no cover
        raise AssertionError("must not warm")

    scheduler = warming.WarmingScheduler(
        warm, warming.WarmingConfig(enabled=False), clock=lambda: 0.0
    )
    assert asyncio.run(scheduler.maybe_warm(100)) is False
