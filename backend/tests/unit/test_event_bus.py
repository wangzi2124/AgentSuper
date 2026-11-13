from __future__ import annotations

import pytest

from app.events.bus import EventBus


@pytest.mark.asyncio
async def test_event_bus_filters_and_delivers() -> None:
    bus = EventBus()
    sub = bus.subscribe({"session.idle"})
    await bus.publish({"id": "1", "type": "session.text.delta", "data": {}})
    assert await sub.get(timeout=0.01) is None
    await bus.publish({"id": "2", "type": "session.idle", "data": {"session_id": "s"}})
    event = await sub.get(timeout=0.1)
    assert event is not None
    assert event["id"] == "2"
    sub.close()
    assert bus.subscriber_count() == 0


@pytest.mark.asyncio
async def test_event_bus_closes_slow_subscriber_on_durable_overflow() -> None:
    bus = EventBus(max_queue=1)
    sub = bus.subscribe()
    await bus.publish({"id": "1", "type": "session.idle", "data": {}, "durable": {"seq": 1}})
    await bus.publish({"id": "2", "type": "session.idle", "data": {}, "durable": {"seq": 2}})
    assert sub.closed is True
    assert bus.subscriber_count() == 0
    assert await sub.get(timeout=0.01) is None


@pytest.mark.asyncio
async def test_event_bus_unsubscribes_on_close() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    assert bus.subscriber_count() == 1
    sub.close()
    await bus.publish({"id": "1", "type": "session.idle", "data": {}})
    assert bus.subscriber_count() == 0
    assert await sub.get(timeout=0.01) is None


@pytest.mark.asyncio
async def test_volatile_overflow_drops_without_closing() -> None:
    bus = EventBus(max_queue=1)
    sub = bus.subscribe()

    await bus.publish({"id": "1", "type": "session.text.delta", "durable": None})
    await bus.publish({"id": "2", "type": "session.text.delta", "durable": None})

    assert sub.closed is False
    assert sub.dropped == 1
    first = await sub.get(timeout=0.01)
    assert first is not None and first["id"] == "1"
