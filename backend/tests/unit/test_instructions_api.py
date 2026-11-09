"""G-01: project instruction override management API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import instructions as api
from prompts.instructions import (
    read_workspace_auto_discover,
    reset_instruction_resolver,
    workspace_instructions_path,
)


class _Sess:
    def __init__(self, directory):
        self.directory = str(directory)


def _client(tmp_path, monkeypatch) -> TestClient:
    ctx = type(
        "Ctx",
        (),
        {
            "sessions": {"s1": _Sess(tmp_path)},
            "session_model": lambda self, session_id: "",
        },
    )()
    monkeypatch.setattr(api, "get_context", lambda request: ctx)
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    return TestClient(app)


def test_add_list_and_remove(tmp_path, monkeypatch):
    reset_instruction_resolver()
    (tmp_path / "guide.md").write_text("GUIDE RULES", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)

    res = client.post(
        "/api/instructions/entries?session_id=s1", json={"entry": "guide.md"}
    )
    assert res.status_code == 200
    assert res.json()["entries"] == ["guide.md"]

    body = client.get("/api/instructions?session_id=s1").json()
    assert body["scope"] == "project"
    assert body["entries"] == ["guide.md"]
    assert body["auto_discover"] is True
    assert any(
        source["kind"] == "config" and source["loaded"] for source in body["sources"]
    )

    res = client.request(
        "DELETE",
        "/api/instructions/entries?session_id=s1",
        json={"entry": "guide.md"},
    )
    assert res.status_code == 200
    assert res.json()["entries"] == []


def test_explicit_workspace_without_session(tmp_path, monkeypatch):
    reset_instruction_resolver()
    (tmp_path / "guide.md").write_text("GUIDE RULES", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        f"/api/instructions/entries?workspace={tmp_path}", json={"entry": "guide.md"}
    )
    assert res.status_code == 200
    assert workspace_instructions_path(tmp_path).is_file()


def test_auto_discover_toggle(tmp_path, monkeypatch):
    reset_instruction_resolver()
    client = _client(tmp_path, monkeypatch)
    res = client.patch(
        "/api/instructions/settings?session_id=s1", json={"auto_discover": False}
    )
    assert res.status_code == 200
    assert res.json()["auto_discover"] is False
    assert read_workspace_auto_discover(tmp_path) is False


def test_rejects_paths_outside_workspace(tmp_path, monkeypatch):
    reset_instruction_resolver()
    outside = tmp_path.parent / "outside-instructions.md"
    outside.write_text("secret", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/instructions/entries?session_id=s1", json={"entry": str(outside)}
    )
    assert res.status_code == 400
