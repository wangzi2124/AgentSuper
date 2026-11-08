"""G-16: attachment URI read and one-level listing."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import attachments


class _Sess:
    def __init__(self, directory):
        self.directory = str(directory)


def _client(tmp_path, monkeypatch):
    ctx = type("Ctx", (), {"sessions": {"s1": _Sess(tmp_path)}})()
    monkeypatch.setattr(attachments, "get_context", lambda request: ctx)
    app = FastAPI()
    app.include_router(attachments.router, prefix="/api/attachments")
    return TestClient(app)


def test_read_uri_and_lines(tmp_path, monkeypatch):
    f = tmp_path / "attachments" / "a.txt"
    f.parent.mkdir()
    f.write_text("l1\nl2\nl3\n", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/attachments/read?session_id=s1",
        json={"uri": f.as_uri() + "#L2-L3", "name": "a"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["text"].strip() == "l2\nl3"
    assert body["name"] == "a"


def test_list_one_level(tmp_path, monkeypatch):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "x.txt").write_text("x", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    body = client.get("/api/attachments/list?session_id=s1").json()
    assert body["data"][0]["name"] == "x.txt"


def test_rejects_escape(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/attachments/read?session_id=s1", json={"uri": "../../etc/passwd"}
    )
    assert res.status_code in (403, 404)
