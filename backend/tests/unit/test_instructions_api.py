"""G-01: project instruction override management API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import instructions as api
from prompts.instructions import reset_instruction_resolver


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


def test_add_requires_session(tmp_path, monkeypatch):
    reset_instruction_resolver()
    client = _client(tmp_path, monkeypatch)
    res = client.post("/api/instructions/entries", json={"entry": "guide.md"})
    assert res.status_code == 400


def test_rejects_paths_outside_workspace(tmp_path, monkeypatch):
    reset_instruction_resolver()
    outside = tmp_path.parent / "outside-instructions.md"
    outside.write_text("secret", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/instructions/entries?session_id=s1", json={"entry": str(outside)}
    )
    assert res.status_code == 400
