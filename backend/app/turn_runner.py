"""Detached turn runner: background execution + replayable event log.

The runner decouples graph execution from the HTTP/SSE connection. A turn keeps
running when the browser refreshes or the network drops; reconnecting clients
subscribe to the same ordered event log (`turn_id` + `seq`) and continue from
``after_seq``.

Only one runner is active per session at a time. Disconnecting a subscriber is
never a cancel: the runner stops only on an explicit ``/interrupt`` (or process
shutdown).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

# SSE heartbeat: subscribers emit one when no event arrived for this long.
HEARTBEAT_SEC = 12.0
# Finished runners stay available for late reconnects before being dropped.
RETENTION_SEC = 60.0

DEFAULT_MAX_EVENTS = 10_000
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

TERMINAL_EVENTS = frozenset({"done", "error"})


def _event_size(event: dict[str, Any]) -> int:
    try:
        return len(json.dumps(event, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:  # noqa: BLE001 - best-effort accounting
        return 512


@dataclass
class EventLog:
    """Bounded in-memory event log for one runner (one drain sequence)."""

    max_events: int = DEFAULT_MAX_EVENTS
    max_bytes: int = DEFAULT_MAX_BYTES
    events: deque[dict[str, Any]] = field(default_factory=deque)
    size_bytes: int = 0
    first_seq: int = 0
    last_seq: int = 0
    truncated: bool = False

    def append(self, event: dict[str, Any]) -> None:
        size = _event_size(event)
        self.events.append(event)
        self.size_bytes += size
        seq = int(event.get("seq") or 0)
        if not self.first_seq:
            self.first_seq = seq
        self.last_seq = max(self.last_seq, seq)
        while self.events and (
            len(self.events) > self.max_events or self.size_bytes > self.max_bytes
        ):
            removed = self.events.popleft()
            self.size_bytes -= _event_size(removed)
            self.truncated = True
        if self.size_bytes < 0:
            self.size_bytes = 0
        if self.events:
            self.first_seq = int(self.events[0].get("seq") or 0)
        else:
            self.first_seq = self.last_seq + 1

    def replay(self, after_seq: int = 0) -> tuple[list[dict[str, Any]], bool, int]:
        """Events with ``seq > after_seq`` plus a gap flag when history was dropped."""
        out = [
            event
            for event in self.events
            if int(event.get("seq") or 0) > max(0, int(after_seq or 0))
        ]
        first_available = self.first_seq or 1
        gap = bool(after_seq > 0 and self.truncated and after_seq < first_available - 1)
        return out, gap, first_available


class TurnRunner:
    """Owns one background drain sequence and its broadcast event log."""

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        *,
        recorder: Any | None = None,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.status = "running"
        self.phase = "running"
        self.started_at = time.time()
        self.finished_at = 0.0
        self.last_seq = 0
        self.log = EventLog(max_events=max_events, max_bytes=max_bytes)
        self.task: asyncio.Task | None = None
        self.recorder = recorder
        self.cancel_event = asyncio.Event()
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()

    @property
    def active(self) -> bool:
        return self.status == "running"

    # ── event production ─────────────────────────────────────────────────
    def emit(self, event: dict[str, Any]) -> dict[str, Any]:
        """Assign identity/seq once, record, and fan out to all subscribers."""
        event = dict(event)
        event.setdefault("turn_id", self.turn_id)
        event.setdefault("session_id", self.session_id)
        if "seq" not in event:
            self.last_seq += 1
            event["seq"] = self.last_seq
        else:
            self.last_seq = max(self.last_seq, int(event.get("seq") or 0))

        self.log.append(event)
        if self.recorder is not None:
            try:
                self.recorder.observe(event)
            except Exception:  # noqa: BLE001 - sidecar must never break the turn
                pass
        for queue in list(self._subscribers):
            queue.put_nowait(event)

        if event.get("type") in TERMINAL_EVENTS:
            self.status = "error" if event.get("type") == "error" else "done"
            self.phase = self.status
            self.finished_at = time.time()
            self._close_subscribers()
        return event

    def finish(self) -> None:
        """Mark finished without emitting an event (used for unexpected exits)."""
        if self.active:
            self.status = "done"
            self.phase = "done"
            self.finished_at = time.time()
        self._close_subscribers()

    def _close_subscribers(self) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    # ── event consumption ────────────────────────────────────────────────
    async def subscribe(self, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Replay retained events newer than ``after_seq``, then tail live events."""
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        replay, gap, first_available = self.log.replay(after_seq)
        finished = not self.active
        # Replay snapshot + registration happen without awaiting, so no event can
        # slip between them (single-threaded event loop).
        self._subscribers.add(queue)
        try:
            if gap and first_available > 1:
                yield {
                    "type": "replay_gap",
                    "turn_id": self.turn_id,
                    "session_id": self.session_id,
                    "seq": first_available - 1,
                    "detail": f"部分历史事件已过期，从 seq {first_available} 继续",
                }
            for event in replay:
                yield event
            if finished:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    yield {
                        "type": "heartbeat",
                        "turn_id": self.turn_id,
                        "session_id": self.session_id,
                    }
                    continue
                if event is None:
                    return
                yield event
                if event.get("type") in TERMINAL_EVENTS:
                    return
        finally:
            self._subscribers.discard(queue)

    def active_turn_info(self) -> dict[str, Any] | None:
        if not self.active:
            return None
        return {
            "turn_id": self.turn_id,
            "last_seq": self.last_seq,
            "phase": self.phase,
            "started_at": self.started_at,
        }
