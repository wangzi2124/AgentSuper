"""进程内事件总线（Phase 3）。

客户端共享一条 SSE；这里只做订阅者广播与过滤。
durable 事件落 event_outbox 后由 emitter 附上 seq。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterable


class EventSubscription:
    def __init__(self, bus: "EventBus", types: Iterable[str] | None, max_queue: int) -> None:
        self._bus = bus
        self._types = set(types) if types else None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self.closed = False
        self.dropped = 0

    def matches(self, event: dict[str, Any]) -> bool:
        return self._types is None or event.get("type") in self._types

    def push(self, event: dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            if event.get("durable") is not None:
                # durable 事件丢失会让客户端状态错位：断开订阅，靠重连 hydration 恢复。
                self.close()
                return False
            # volatile（文本/进度增量）可丢，最终 durable 事件会补全文本，不必断连。
            self.dropped += 1
            return True

    async def get(self, timeout: float | None = None) -> dict[str, Any] | None:
        if self.closed:
            return None
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._bus._drop(self)

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while not self.closed:
            event = await self.get()
            if event is None:
                return
            yield event


class EventBus:
    def __init__(self, *, max_queue: int = 1000) -> None:
        self._subscribers: dict[EventSubscription, None] = {}
        self._max_queue = max(1, int(max_queue))

    def subscribe(self, types: Iterable[str] | None = None) -> EventSubscription:
        sub = EventSubscription(self, types, self._max_queue)
        self._subscribers[sub] = None
        return sub

    def _drop(self, sub: EventSubscription) -> None:
        self._subscribers.pop(sub, None)

    async def publish(self, event: dict[str, Any]) -> None:
        for sub in list(self._subscribers):
            if not sub.matches(event):
                continue
            if not sub.push(event):
                sub.close()

    def subscriber_count(self) -> int:
        return len(self._subscribers)
