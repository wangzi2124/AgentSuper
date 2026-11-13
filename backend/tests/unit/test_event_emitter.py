"""emit_event：热路径不查库，durable 写库不阻塞事件循环。"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from app.events import emitter


class _Bus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


def _ctx(*, sessions):
    return SimpleNamespace(events=_Bus(), sessions=sessions)


async def test_volatile_event_location_uses_memory_session(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("repo.get_session must not run on the delta hot path")

    monkeypatch.setattr(emitter.repo, "get_session", boom)
    sessions = SimpleNamespace(
        get=lambda _sid: SimpleNamespace(directory="D:/ws", project_id="p1")
    )
    ctx = _ctx(sessions=sessions)

    event = await emitter.emit_event(
        ctx,
        "session.text.delta",
        {
            "session_id": "ses_1",
            "assistant_message_id": "am_1",
            "ordinal": 0,
            "delta": "x",
        },
        session_id="ses_1",
    )

    assert event is not None
    assert event["location"] == {"directory": "D:/ws", "project_id": "p1"}
    assert ctx.events.events[0]["type"] == "session.text.delta"


async def test_durable_append_runs_off_event_loop(monkeypatch):
    seen: dict[str, int] = {}

    def append_event(*_args):
        seen["thread"] = threading.get_ident()
        return 7

    monkeypatch.setattr(emitter.repo, "append_event", append_event)
    sessions = SimpleNamespace(
        get=lambda _sid: SimpleNamespace(directory="D:/ws", project_id="p1")
    )
    ctx = _ctx(sessions=sessions)

    event = await emitter.emit_event(
        ctx,
        "session.created",
        {
            "session": {
                "id": "ses_1",
                "project_id": "p1",
                "directory": "D:/ws",
                "time": {"created": 1, "updated": 1},
            }
        },
        session_id="ses_1",
    )

    assert event is not None
    assert event["durable"] == {"aggregate_id": "ses_1", "seq": 7, "version": 2}
    assert seen["thread"] != threading.get_ident()
