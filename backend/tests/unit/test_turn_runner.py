from __future__ import annotations

import asyncio

import pytest

from app.turn_runner import EventLog, TurnRunner


def test_event_log_bounds_and_replay_gap():
    log = EventLog(max_events=3, max_bytes=10_000)
    for seq in range(1, 6):
        log.append({"type": "routing", "seq": seq, "payload": "x" * 10})

    events, gap, first_available = log.replay(0)
    assert [e["seq"] for e in events] == [3, 4, 5]
    assert gap is False
    assert first_available == 3

    events, gap, _ = log.replay(1)
    assert [e["seq"] for e in events] == [3, 4, 5]
    assert gap is True

    events, gap, _ = log.replay(2)
    assert [e["seq"] for e in events] == [3, 4, 5]
    assert gap is False

    events, gap, _ = log.replay(4)
    assert [e["seq"] for e in events] == [5]
    assert gap is False


def test_emit_assigns_identity_and_sequence():
    runner = TurnRunner("s1", "turn_1")
    first = runner.emit({"type": "routing"})
    second = runner.emit({"type": "text_delta", "delta": "hi"})

    assert first["turn_id"] == "turn_1"
    assert first["session_id"] == "s1"
    assert first["seq"] == 1
    assert second["seq"] == 2
    assert runner.last_seq == 2
    assert runner.active_turn_info()["last_seq"] == 2


@pytest.mark.asyncio
async def test_subscribe_replays_then_tails_terminal():
    runner = TurnRunner("s1", "turn_1")
    runner.emit({"type": "routing"})
    runner.emit({"type": "text_delta", "delta": "答"})

    async def produce():
        await asyncio.sleep(0)
        runner.emit({"type": "done", "answer": "答案"})

    task = asyncio.create_task(produce())
    seen = [event async for event in runner.subscribe(0)]
    await task

    assert [e["type"] for e in seen] == ["routing", "text_delta", "done"]
    assert [e["seq"] for e in seen] == [1, 2, 3]
    assert runner.active is False


@pytest.mark.asyncio
async def test_subscribe_after_seq_only_replays_newer():
    runner = TurnRunner("s1", "turn_1")
    runner.emit({"type": "routing"})
    runner.emit({"type": "done", "answer": "ok"})

    seen = [event async for event in runner.subscribe(1)]
    assert [e["seq"] for e in seen] == [2]


def test_cancel_and_finish():
    runner = TurnRunner("s1", "turn_1")
    assert runner.active is True
    runner.finish()
    assert runner.active is False
    assert runner.active_turn_info() is None
    assert runner.cancel_event.is_set() is False
