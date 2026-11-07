from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.session import repository as repo
from execution.filesystem import VirtualFileSystem
from models.router import ChatResult


def _create(client: TestClient, title: str = "v2 migration") -> str:
    response = client.post("/api/v2/session", json={"title": title})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def _prompt(client: TestClient, session_id: str, text: str) -> None:
    response = client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={"text": text, "resume": True, "delivery": "steer"},
    )
    assert response.status_code == 200, response.text


def _wait(client: TestClient, session_id: str, *, users: int = 1, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/v2/session/{session_id}").json()["data"]
        messages = client.get(f"/api/v2/session/{session_id}/message").json()["data"]
        user_count = sum(1 for message in messages if message["type"] == "user")
        assistant_count = sum(1 for message in messages if message["type"] == "assistant")
        if (
            last["status"] == "idle"
            and last.get("outcome") is not None
            and user_count >= users
            and assistant_count >= users
        ):
            return last
        time.sleep(0.05)
    raise AssertionError(f"session did not settle: {last}")


def test_v2_queue_drains_prompts(client: TestClient) -> None:
    session_id = _create(client)
    _prompt(client, session_id, "first queued prompt")
    _prompt(client, session_id, "second queued prompt")
    _wait(client, session_id, users=2)
    messages = client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    user_texts = [message["text"] for message in messages if message["type"] == "user"]
    assert user_texts == ["first queued prompt", "second queued prompt"]
    assert sum(1 for message in messages if message["type"] == "assistant") >= 2


class UsageFakeModel:
    tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(
            content="usage answer",
            finish_reason="stop",
            model="fake-model",
            usage={"input": 12, "output": 7, "total": 19},
        )


@pytest.fixture
def usage_client(tmp_path, monkeypatch):
    from graph import usage as usage_mod

    monkeypatch.setattr(usage_mod, "estimate_cost", lambda m, pt, ct: pt * 1e-6 + ct * 2e-6)
    app = create_app(model=UsageFakeModel(), vfs=VirtualFileSystem(root=str(tmp_path)))
    with TestClient(app) as client:
        yield client


def test_v2_usage_accumulates_across_turns(usage_client: TestClient) -> None:
    session_id = _create(usage_client)
    _prompt(usage_client, session_id, "first usage turn")
    first = _wait(usage_client, session_id)
    assert first["tokens"]["input"] == 12
    assert first["tokens"]["output"] == 7
    assert first["cost"] == pytest.approx(12e-6 + 14e-6)

    _prompt(usage_client, session_id, "second usage turn")
    second = _wait(usage_client, session_id, users=2)
    assert second["tokens"]["input"] == 24
    assert second["tokens"]["output"] == 14
    assert second["cost"] == pytest.approx(2 * (12e-6 + 14e-6))


def test_v2_session_messages_replay(client: TestClient) -> None:
    session_id = _create(client)
    _prompt(client, session_id, "replay check")
    _wait(client, session_id)
    messages = client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    assert messages
    assert all(message.get("id") and message.get("type") for message in messages)
    assert messages[0]["type"] == "user"
    assert any(message["type"] == "assistant" for message in messages)


def test_v2_model_selection_emits_event(client: TestClient) -> None:
    session_id = _create(client)
    response = client.post(
        f"/api/v2/session/{session_id}/model",
        json={
            "model": {
                "id": "deepseek/deepseek-v4-flash",
                "provider_id": "",
                "variant": None,
            }
        },
    )
    assert response.status_code == 200, response.text
    events = {event["type"] for event in repo.list_events(session_id)}
    assert "session.model.selected" in events
