"""Real-model feature checks for Web Chat v2.

Run manually:
  $env:HARNESS_REAL_TEST='1'
  $env:HARNESS_REAL_MODEL='deepseek/deepseek-v4-flash'
  pytest tests/integration/test_v2_real_features.py -q -s
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from config import settings
from app.session import repository as repo

pytestmark = pytest.mark.skipif(
    not bool(settings.harness_real_test),
    reason="set HARNESS_REAL_TEST=1 to run real-model feature checks",
)

MODEL = (settings.harness_real_model or "deepseek/deepseek-v4-flash").strip()


def _skip_without_key():
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY not configured")


def _wait(client: TestClient, session_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/v2/session/{session_id}").json()["data"]
        if last["status"] == "idle" and last.get("outcome") is not None:
            return last
        time.sleep(0.2)
    raise AssertionError(f"session did not settle: {last}")


def _prompt(client: TestClient, session_id: str, text: str) -> None:
    response = client.post(
        f"/api/v2/session/{session_id}/prompt",
        json={"text": text, "resume": True, "delivery": "steer"},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def real_client(tmp_path, monkeypatch):
    _skip_without_key()
    app = create_app(include_app=False)
    with TestClient(app) as client:
        yield client


def _new_session(client: TestClient) -> str:
    response = client.post(
        "/api/v2/session",
        json={
            "title": "real feature check",
            "model": {"id": MODEL, "provider_id": "", "variant": None},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_real_filesystem_write_and_read(real_client: TestClient) -> None:
    session_id = _new_session(real_client)
    info = real_client.get(f"/api/v2/session/{session_id}").json()["data"]
    _prompt(
        real_client,
        session_id,
        "Use the write_file tool to create REAL_CHECK.txt in the current workspace "
        "with exactly the content OPENCODE-REAL-OK. Then use read_file to read it. "
        "Reply with DONE only.",
    )
    _wait(real_client, session_id)
    target = Path(info["directory"]) / "REAL_CHECK.txt"
    assert target.exists(), f"model did not create {target}"
    assert target.read_text(encoding="utf-8").strip() == "OPENCODE-REAL-OK"
    messages = real_client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    assert any(message["type"] == "assistant" for message in messages)
    tools = [
        part
        for message in messages
        if message["type"] == "assistant"
        for part in message["content"]
        if part["type"] == "tool"
    ]
    assert any(part["name"] in {"write_file", "read_file"} for part in tools)


def _wait_compaction(session_id: str, timeout: float = 180.0) -> list[dict]:
    deadline = time.time() + timeout
    last: list[dict] = []
    while time.time() < deadline:
        last = repo.list_events(session_id)
        types = {event["type"] for event in last}
        if "session.compaction.ended" in types or "session.compaction.failed" in types:
            return last
        time.sleep(0.2)
    return last


def test_real_compaction(real_client: TestClient) -> None:
    session_id = _new_session(real_client)
    _prompt(real_client, session_id, "Explain in one sentence what a filesystem is.")
    _wait(real_client, session_id)
    _prompt(real_client, session_id, "Explain in one sentence what a process is.")
    _wait(real_client, session_id)

    compacted = real_client.post(f"/api/v2/session/{session_id}/compact")
    assert compacted.status_code == 200, compacted.text
    events = _wait_compaction(session_id)
    types = {event["type"] for event in events}
    assert "session.compaction.started" in types
    assert "session.compaction.failed" not in types, events
    ended = next(event for event in events if event["type"] == "session.compaction.ended")
    assert ended["data"]["text"].strip(), events
    messages = real_client.get(f"/api/v2/session/{session_id}/message").json()["data"]
    assert any(message["type"] == "compaction" for message in messages), messages


def test_real_permission_denial(real_client: TestClient) -> None:
    session_id = _new_session(real_client)
    ctx = real_client.app.state.platform
    calls: list[str] = []

    async def deny_write(*, session_id, tool, args, emit):
        calls.append(tool)
        if tool in {"write_file", "append_file", "edit_file", "delete_file"}:
            await emit({"type": "permission_request", "tool_name": tool})
            return False
        return True

    ctx.permission_hook = deny_write
    try:
        _prompt(
            real_client,
            session_id,
            "Use the write_file tool to create DENIED.txt in the current workspace "
            "with content DENIED. If the tool is denied, reply DENIED.",
        )
        _wait(real_client, session_id)
    finally:
        ctx.permission_hook = None
    info = real_client.get(f"/api/v2/session/{session_id}").json()["data"]
    assert not (Path(info["directory"]) / "DENIED.txt").exists()
    assert "write_file" in calls
