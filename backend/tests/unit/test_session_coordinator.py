"""ExecutionCoordinator: 唤醒不丢失与 interrupt 语义回归。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.session.coordinator import ExecutionCoordinator


class _Sessions:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []

    def update(self, session_id: str, **fields):  # noqa: ANN001
        self.updates.append((session_id, fields))


class _Ctx:
    def __init__(self) -> None:
        self.sessions = _Sessions()
        self.active: set[str] = set()

    def cancel_turn(self, session_id: str) -> bool:
        if session_id in self.active:
            self.active.discard(session_id)
            return True
        return False


class _Repo:
    def __init__(self) -> None:
        self.pending: list[dict] = []
        self.statuses: list[tuple[str, str, str | None]] = []

    def promote_next(self, session_id: str):  # noqa: ANN001
        if not self.pending:
            return None
        return self.pending.pop(0)

    def list_inputs(self, session_id: str):  # noqa: ANN001
        return list(self.pending)

    def set_session_status(self, session_id: str, status: str, outcome: str | None = None):
        self.statuses.append((session_id, status, outcome))


def _item(item_id: str, delivery: str = "queue") -> dict:
    return {"id": item_id, "delivery": delivery, "type": "user", "prompt": {}}


@pytest.fixture
def env(monkeypatch):
    import app.events.emitter as emitter
    import app.session.repository as repository

    repo = _Repo()
    ctx = _Ctx()
    events: list[str] = []

    monkeypatch.setattr(repository, "promote_next", lambda sid: repo.promote_next(sid))
    monkeypatch.setattr(repository, "list_inputs", lambda sid: repo.list_inputs(sid))
    monkeypatch.setattr(repository, "set_session_status", repo.set_session_status)

    async def fake_emit(_ctx, event_type, _payload, session_id=None):  # noqa: ANN001
        events.append(event_type)

    monkeypatch.setattr(emitter, "emit_event", fake_emit)
    return SimpleNamespace(repo=repo, ctx=ctx, events=events)


async def _wait_idle(coord: ExecutionCoordinator, session_id: str) -> None:
    for _ in range(200):
        if not coord.is_running(session_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("drain did not finish")


async def test_wake_during_finish_is_not_lost(env):
    coord = ExecutionCoordinator(env.ctx)
    sid = "ses_wake"
    runs: list[str] = []
    finish_calls = {"n": 0}

    async def run_item(_sid, item):  # noqa: ANN001
        runs.append(item["id"])

    async def finish(_sid, _outcome):  # noqa: ANN001
        finish_calls["n"] += 1
        if finish_calls["n"] == 1:
            # 让出事件循环：模拟「drain 仍存活时 wake 到达」的窗口。
            await asyncio.sleep(0)

    coord._run_item = run_item
    coord._finish = finish

    def promote(_sid):  # noqa: ANN001
        if env.repo.pending:
            return env.repo.pending.pop(0)
        if finish_calls["n"] == 0:
            # 空队列检查时新 prompt 入队并唤醒，此时 drain 任务尚未注销。
            env.repo.pending.append(_item("in_2", "steer"))
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(coord.wake(sid))
            )
        return None

    env.repo.promote_next = promote
    env.repo.pending.append(_item("in_1"))

    await coord.wake(sid)
    await _wait_idle(coord, sid)

    assert runs == ["in_1", "in_2"]


async def test_interrupt_while_idle_does_not_mark(env):
    coord = ExecutionCoordinator(env.ctx)
    assert coord.interrupt("ses_idle") is False
    assert "ses_idle" not in coord._interrupt


async def test_interrupt_while_active_records_mode(env):
    coord = ExecutionCoordinator(env.ctx)
    env.ctx.active.add("ses_active")

    assert coord.interrupt("ses_active", resume=True) is True
    assert coord._interrupt["ses_active"] == "resume"
    assert env.ctx.active == set()


async def test_interrupt_stop_leaves_queue_pending(env):
    coord = ExecutionCoordinator(env.ctx)
    sid = "ses_stop"
    runs: list[str] = []

    async def run_item(_sid, item):  # noqa: ANN001
        runs.append(item["id"])

    coord._run_item = run_item
    env.repo.pending = [_item("in_1"), _item("in_2")]
    env.ctx.active.add(sid)
    assert coord.interrupt(sid) is True

    await coord.wake(sid)
    await _wait_idle(coord, sid)

    assert runs == ["in_1"]
    assert [item["id"] for item in env.repo.pending] == ["in_2"]
    assert "session.execution.interrupted" in env.events


async def test_interrupt_resume_continues_steer_but_not_queue(env):
    coord = ExecutionCoordinator(env.ctx)
    sid = "ses_resume"
    runs: list[str] = []

    async def run_item(_sid, item):  # noqa: ANN001
        runs.append(item["id"])

    coord._run_item = run_item
    env.repo.pending = [
        _item("steer_1", "steer"),
        _item("steer_2", "steer"),
        _item("queue_3", "queue"),
    ]
    env.ctx.active.add(sid)
    assert coord.interrupt(sid, resume=True) is True

    await coord.wake(sid)
    await _wait_idle(coord, sid)

    assert runs == ["steer_1", "steer_2"]
    assert [item["id"] for item in env.repo.pending] == ["queue_3"]
