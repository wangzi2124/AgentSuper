from __future__ import annotations

import json
import threading
import time

import pytest

from app.turn_runner import TurnRunner


def _events(body: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _finish_later(client, runner: TurnRunner, delay: float = 0.2) -> threading.Thread:
    def _job() -> None:
        time.sleep(delay)
        client.portal.call(runner.emit, {"type": "done", "answer": "后台完成"})

    thread = threading.Thread(target=_job, daemon=True)
    thread.start()
    return thread


def test_status_exposes_active_turn_and_resume_replays(client):
    ctx = client.app.state.platform
    session_id = client.post("/api/sessions", json={}).json()["id"]
    runner = TurnRunner(session_id, "turn_seed_1")
    runner.emit({"type": "model_switched", "model_ref": {"id": "m", "name": "m"}})
    runner.emit({"type": "routing", "detail": "..."})
    ctx.register_runner(runner)

    status = client.get(f"/api/sessions/{session_id}/status").json()
    assert status["active_turn"]["turn_id"] == "turn_seed_1"
    assert status["active_turn"]["last_seq"] == 2

    # 结束前先取“重连快照”：GET 会阻塞到终态，所以只重放 seq>1 的历史。
    runner.emit({"type": "done", "answer": "后台完成"})
    body = client.get(
        f"/api/chat/multi-agent/stream/{session_id}", params={"after_seq": 1}
    ).text
    events = _events(body)
    assert [e["type"] for e in events] == ["routing", "done"]
    assert [e["seq"] for e in events] == [2, 3]
    assert {e["turn_id"] for e in events} == {"turn_seed_1"}
    assert events[-1]["answer"] == "后台完成"

    assert client.get(f"/api/sessions/{session_id}/status").json()["active_turn"] is None


def test_post_while_turn_is_active_enqueues_and_subscribes(client):
    ctx = client.app.state.platform
    session_id = client.post("/api/sessions", json={}).json()["id"]
    runner = TurnRunner(session_id, "turn_seed_2")
    runner.emit({"type": "routing", "detail": "..."})
    ctx.register_runner(runner)

    thread = _finish_later(client, runner)
    response = client.post(
        "/api/chat/multi-agent/stream",
        json={"message": "第二条", "conversation_id": session_id, "client_msg_id": "cm_2"},
    )
    thread.join(timeout=5)

    assert response.status_code == 200
    assert response.headers["X-Session-Id"] == session_id
    events = _events(response.text)
    assert events[-1]["type"] == "done"
    assert {e["turn_id"] for e in events} == {"turn_seed_2"}

    sess = ctx.sessions.get(session_id)
    assert any(p.get("id") == "cm_2" for p in sess.prompts)


@pytest.mark.asyncio
async def test_unsubscribe_does_not_cancel_runner():
    runner = TurnRunner("s1", "turn_1")
    runner.emit({"type": "routing"})
    stream = runner.subscribe(0)
    assert (await anext(stream))["type"] == "routing"
    await stream.aclose()

    assert runner.active is True
    runner.emit({"type": "done", "answer": "ok"})
    assert runner.active is False


def test_resume_unknown_session_is_404(client):
    assert client.get("/api/chat/multi-agent/stream/nope").status_code == 404
