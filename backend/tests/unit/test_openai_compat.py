"""G-19: OpenAI-compatible chat completions facade."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import openai_compat


class _Session:
    id = "ses_test"
    model = "old"


class _Ctx:
    def __init__(self) -> None:
        self.sessions = {"ses_test": _Session()}
        self.switched = []

    def create(self, **_kwargs):
        return _Session()

    async def switch_model(self, session_id, model):
        self.switched.append((session_id, model))

    async def run(self, question, *, session_id, **_kwargs):
        return {"final_output": f"echo:{question}"}

    async def stream(self, question, *, session_id, **_kwargs):
        yield {"type": "text_delta", "data": {"delta": "hello"}}
        yield {"type": "text_delta", "data": {"delta": " world"}}


def _client(monkeypatch) -> TestClient:
    ctx = _Ctx()
    monkeypatch.setattr(openai_compat, "get_context", lambda request: ctx)
    app = FastAPI()
    app.include_router(openai_compat.router)
    return TestClient(app)


def test_non_stream(monkeypatch):
    client = _client(monkeypatch)
    body = client.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    ).json()
    assert body["choices"][0]["message"]["content"] == "echo:hi"


def test_rejects_empty(monkeypatch):
    client = _client(monkeypatch)
    res = client.post("/v1/chat/completions", json={"messages": []})
    assert res.status_code == 400


def test_stream(monkeypatch):
    client = _client(monkeypatch)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    ) as res:
        text = "".join(res.iter_text())
    assert '"content": "hello"' in text
    assert "data: [DONE]" in text


def test_existing_session_model_switch(monkeypatch):
    client = _client(monkeypatch)
    res = client.post(
        "/v1/chat/completions",
        headers={"X-Session-Id": "ses_test"},
        json={"model": "new-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert res.status_code == 200
