from __future__ import annotations

import base64
import time

from app.session import repository as repo


def _wait_idle(client, session_id: str, timeout: float = 8.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/v2/session/{session_id}").json()["data"]
        if last["status"] == "idle" and last["outcome"] is not None:
            return last
        time.sleep(0.05)
    return last


def test_v2_prompt_runs_and_persists_messages(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 api"})
    assert created.status_code == 200
    session_id = created.json()["data"]["id"]
    assert any(event["type"] == "session.created" for event in repo.list_events(session_id))

    admitted = client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={"id": "msg_user_v2", "text": "请检查仓库结构", "resume": True},
    )
    assert admitted.status_code == 200
    assert admitted.json()["data"]["id"] == "msg_user_v2"

    session = _wait_idle(client, session_id)
    assert session["status"] == "idle"
    assert session["outcome"] == "succeeded"
    event_types = {event["type"] for event in repo.list_events(session_id)}
    assert {"session.inbox.enqueued", "session.execution.started", "session.inbox.delivered"} <= event_types
    assert {"session.step.started", "session.step.ended"} <= event_types

    messages = client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    types = [message["type"] for message in messages]
    assert types[0] == "user"
    assert "assistant" in types
    assistant = next(message for message in messages if message["type"] == "assistant")
    assert any(part["type"] == "text" for part in assistant["content"])


def test_v2_prompt_accepts_attachment_data(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 attach"})
    session_id = created.json()["data"]["id"]
    payload = base64.b64encode(b"hello attachment").decode()
    admitted = client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={
            "id": "msg_attach",
            "text": "请看附件",
            "resume": True,
            "files": [
                {
                    "type": "file",
                    "uri": "note.txt",
                    "name": "note.txt",
                    "mime": "text/plain",
                    "data": payload,
                }
            ],
        },
    )
    assert admitted.status_code == 200
    _wait_idle(client, session_id)
    messages = client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    user = messages[0]
    assert user["files"][0]["filename"] == "note.txt"


def test_v2_prompt_keeps_voice_metadata(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 voice"})
    session_id = created.json()["data"]["id"]
    admitted = client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={
            "id": "msg_voice",
            "text": "语音消息",
            "resume": True,
            "voice": {
                "id": "voice_1",
                "url": "/api/voice/audio/voice_1.webm",
                "duration": 2.5,
                "waveform": [0.1, 0.4],
                "text": "语音消息",
            },
        },
    )
    assert admitted.status_code == 200
    _wait_idle(client, session_id)
    messages = client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    assert messages[0]["voice"]["id"] == "voice_1"


def test_v2_prompt_without_resume_stays_in_inbox_and_can_cancel(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 inbox"})
    session_id = created.json()["data"]["id"]

    admitted = client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={"id": "msg_waiting", "text": "先别执行检查", "resume": False},
    )
    assert admitted.status_code == 200
    inbox = client.get(f"/api/v2/session/{session_id}/inbox").json()["data"]
    assert [item["id"] for item in inbox] == ["msg_waiting"]

    updated = client.patch(
        f"/api/v2/session/{session_id}/inbox/msg_waiting",
        json={"delivery": "queue"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["item"]["delivery"] == "queue"

    cancelled = client.delete(f"/api/v2/session/{session_id}/inbox/msg_waiting")
    assert cancelled.status_code == 204
    assert client.get(f"/api/v2/session/{session_id}/inbox").json()["data"] == []


def test_v2_revert_stage_commit_clears_after_user_message(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 revert"})
    session_id = created.json()["data"]["id"]
    client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={"id": "msg_revert", "text": "hello", "resume": True},
    )
    _wait_idle(client, session_id)

    staged = client.post(
        f"/api/v2/session/{session_id}/revert/stage",
        json={"message_id": "msg_revert"},
    )
    assert staged.status_code == 200
    assert staged.json()["data"]["message_id"] == "msg_revert"

    committed = client.post(f"/api/v2/session/{session_id}/revert/commit")
    assert committed.status_code == 200
    assert committed.json()["data"]["deleted"] >= 1
    session = client.get(f"/api/v2/session/{session_id}").json()["data"]
    assert session["id"] == session_id


def test_v2_session_list_and_get(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 list"})
    session_id = created.json()["data"]["id"]
    listed = client.get("/api/v2/session?limit=20").json()["data"]
    assert any(item["id"] == session_id for item in listed)
    fetched = client.get(f"/api/v2/session/{session_id}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["title"] == "v2 list"


def test_v2_diff_and_restore_without_snapshot(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 diff"})
    session_id = created.json()["data"]["id"]
    diff = client.post(
        f"/api/v2/session/{session_id}/diff",
        json={"message_id": "missing"},
    )
    assert diff.status_code == 200
    assert diff.json()["data"]["files"] == []
    restored = client.post(
        f"/api/v2/session/{session_id}/restore",
        json={"message_id": "missing"},
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["restored"] is False


def test_v2_event_endpoint_is_registered(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/event" in paths


def test_v2_permission_and_form_endpoints(client) -> None:
    created = client.post("/api/v2/session", json={"title": "v2 perm"})
    session_id = created.json()["data"]["id"]
    repo.create_permission(
        session_id, "perm_api", "edit", ["E:/AgentSuper/a.py"], message="allow?"
    )
    listed = client.get(f"/api/v2/session/{session_id}/permission").json()["data"]
    assert [item["id"] for item in listed] == ["perm_api"]

    replied = client.post(
        f"/api/v2/session/{session_id}/permission/perm_api/reply",
        json={"decision": "always"},
    )
    assert replied.status_code == 204
    assert repo.list_permissions(session_id, status=None)[0]["status"] == "replied"

    repo.create_form(session_id, "form_api", [{"id": "pm", "type": "select"}])
    forms = client.get(f"/api/v2/session/{session_id}/form").json()["data"]
    assert [item["id"] for item in forms] == ["form_api"]
    form_reply = client.post(
        f"/api/v2/session/{session_id}/form/form_api/reply",
        json={"response": {"pm": "pnpm"}},
    )
    assert form_reply.status_code == 204
    assert client.get(f"/api/v2/session/{session_id}/form").json()["data"][0]["state"] == "replied"
