"""L3 E2E 契约测试：HTTP 接口形状（agent.md §9 / §10.4 / §10.8）。

用假模型跑通真实 FastAPI 层，断言：
- `POST /chat` 请求 `{"message": ...}` → 响应含 `"output"`；
- 复用 `thread_id` 时 `"resumed"` 为 `True`（§10.8 可靠性）；
- `/api/chat*` 与裸 `/chat*` 为同一契约；
- `/chat/stream` 推送 `start`/`node`/`done` SSE 事件。
"""

from __future__ import annotations


def test_chat_contract_returns_output(client):
    resp = client.post("/chat", json={"message": "1+1=?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["output"] == "最终答案"
    assert body["thread_id"]
    assert body["resumed"] is False


def test_chat_thread_id_resumes(client):
    first = client.post("/chat", json={"message": "hi"}).json()
    tid = first["thread_id"]
    second = client.post("/chat", json={"message": "again", "thread_id": tid}).json()
    assert second["resumed"] is True
    assert second["thread_id"] == tid
    assert second["output"]


def test_api_prefix_same_contract(client):
    body = client.post("/api/chat", json={"message": "hi"}).json()
    assert "output" in body
    assert "thread_id" in body


def test_chat_stream_events(client):
    with client.stream("POST", "/chat/stream", json={"message": "hi"}) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "event: start" in text
    assert "event: node" in text
    assert "event: done" in text


def test_health_and_root(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/").json()["service"]


def test_validation_error_is_enveloped(client):
    # message 不能为空（min_length=1）→ 统一信封 {code:422}
    resp = client.post("/chat", json={"message": ""})
    assert resp.status_code == 422
    assert resp.json()["code"] == 422
