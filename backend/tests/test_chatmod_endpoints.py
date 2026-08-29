# -*- coding: utf-8 -*-
"""chatmod/endpoints.py 全量用例：信号量、chat_multi_agent 成功/取消/错误/回复错误、
chat_multi_agent_stream 成功流/排队满/回复错误/超时（mock 依赖）。

运行：pytest tests/test_chatmod_endpoints.py
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi import HTTPException

import app.api.chatmod.endpoints as ep
from app.agent.base import AgentMessage
from app.models.schemas import ChatRequest


# ── 信号量 ─────────────────────────────────────────────────────────────────

def test_get_agent_semaphore_creates_once(monkeypatch):
    monkeypatch.setattr(ep, "_agent_semaphore", None)
    s1 = ep._get_agent_semaphore()
    s2 = ep._get_agent_semaphore()
    assert s1 is s2
    assert s1._value == ep.MAX_CONCURRENT_AGENTS


# ── 基建 ───────────────────────────────────────────────────────────────────

def _req(agent_bus, session_service):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_bus=agent_bus, session_service=session_service)),
        headers={"X-User-Id": "u1"},
        client=SimpleNamespace(host="1.2.3.4"),
        url=SimpleNamespace(path="/api/chat/multi-agent"),
    )


def _body(message="hello", **kw):
    base = dict(message=message, conversation_id=None, model=None, use_vector_db=False,
                files=[], directory="", client_msg_id=None)
    base.update(kw)
    return ChatRequest(**base)


class FakeBus:
    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    async def send_and_wait(self, msg, timeout=None):
        self.calls.append((msg, timeout))
        if self.exc:
            raise self.exc
        return self.reply


@pytest.fixture
def env(monkeypatch):
    """mock endpoints 依赖：resolve/build/persist/begin。"""
    svc = SimpleNamespace(update=lambda *a, **k: None)
    monkeypatch.setattr(ep, "_resolve_multi_agent_parent",
                        lambda req, uid, cid, directory="": (svc, "s1", "/dir"))
    monkeypatch.setattr(ep, "_begin_task_session",
                        lambda svc, uid, parent, q: ("child1", "thread1"))

    async def fake_persist(*a, **k):
        return ("um1", "am1")
    monkeypatch.setattr(ep, "_persist_multi_agent", fake_persist)

    async def fake_history(svc, uid, sid):
        return []
    monkeypatch.setattr(ep, "_build_compressed_history", fake_history)
    return svc


def _ok_reply():
    return AgentMessage(source="supervisor", target="user", type="response", action="chat",
                        payload={"answer": "A", "sources": [], "steps": [], "routed_to": "rag"})


# ── chat_multi_agent ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_multi_agent_ok(env, monkeypatch):
    bus = FakeBus(_ok_reply())
    resp = await ep.chat_multi_agent(_req(bus, object()), _body())
    assert resp.answer == "A"
    assert resp.conversation_id == "s1"
    assert resp.routed_to == "rag"
    msg, timeout = bus.calls[0]
    assert msg.target == "supervisor"
    assert msg.payload["question"] == "hello"
    assert msg.payload["conversation_id"] == "s1"


@pytest.mark.asyncio
async def test_chat_multi_agent_blank_message(env):
    with pytest.raises(HTTPException) as e:
        await ep.chat_multi_agent(_req(FakeBus(), object()), ChatRequest.model_construct(message=" "))
    assert e.value.status_code == 422


@pytest.mark.asyncio
async def test_chat_multi_agent_reply_error(env, monkeypatch, caplog):
    bus = FakeBus(AgentMessage(source="supervisor", target="user", type="error", action="chat",
                               payload={"error": "sub failed"}))
    with pytest.raises(HTTPException) as e:
        await ep.chat_multi_agent(_req(bus, object()), _body())
    assert e.value.status_code == 500


@pytest.mark.asyncio
async def test_chat_multi_agent_cancelled(env, monkeypatch):
    bus = FakeBus(exc=asyncio.CancelledError())
    with pytest.raises(HTTPException) as e:
        await ep.chat_multi_agent(_req(bus, object()), _body())
    assert e.value.status_code == 499


@pytest.mark.asyncio
async def test_chat_multi_agent_internal_error(env, monkeypatch):
    bus = FakeBus(exc=RuntimeError("boom"))
    with pytest.raises(HTTPException) as e:
        await ep.chat_multi_agent(_req(bus, object()), _body())
    assert e.value.status_code == 500


# ── chat_multi_agent_stream ────────────────────────────────────────────────

async def _drain_stream(resp):
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c)
    return chunks


def _parse_sse(chunks):
    events = []
    for c in chunks:
        text = c.decode("utf-8") if isinstance(c, bytes) else c
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


@pytest.mark.asyncio
async def test_stream_ok(env, monkeypatch):
    bus = FakeBus(_ok_reply())
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    assert resp.media_type == "text/event-stream"
    events = _parse_sse(await _drain_stream(resp))
    types = [e["type"] for e in events]
    assert "routing" in types
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "A"
    assert events[-1]["conversation_id"] == "s1"
    assert events[-1]["user_msg_id"] == "um1"


@pytest.mark.asyncio
async def test_stream_reply_error(env, monkeypatch):
    bus = FakeBus(AgentMessage(source="supervisor", target="user", type="error", action="chat",
                               payload={"error": "boom"}))
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "AgentError"


@pytest.mark.asyncio
async def test_stream_timeout(env, monkeypatch):
    async def boom_send(msg, timeout=None):
        raise asyncio.TimeoutError()
    bus = FakeBus()
    bus.send_and_wait = boom_send
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    assert events[-1]["type"] == "error"
    assert events[-1]["error_type"] == "TimeoutError"
    assert events[-1]["retryable"] is True


@pytest.mark.asyncio
async def test_stream_queue_full(env, monkeypatch):
    monkeypatch.setattr(ep, "_queue_counter", ep.MAX_QUEUE_SIZE)  # 排队已满
    monkeypatch.setattr(ep, "_get_agent_semaphore", lambda: _FullSemaphore())
    resp = await ep.chat_multi_agent_stream(_req(FakeBus(), object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    assert events[0]["type"] == "error"
    assert events[0]["status_code"] == 429


class _FullSemaphore:
    def locked(self):
        return True


@pytest.mark.asyncio
async def test_stream_queued_then_runs(env, monkeypatch):
    # 信号量在进入时已满 → 发 queued，进入后递减计数
    import app.api.chatmod.endpoints as ep_mod
    real = ep_mod._get_agent_semaphore()
    state = {"locked": True, "entered": False}

    class FlakySem:
        def locked(self):
            return True if not state["entered"] else False

        async def __aenter__(self):
            state["entered"] = True
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ep, "_get_agent_semaphore", lambda: FlakySem())
    monkeypatch.setattr(ep, "_queue_counter", 0)
    bus = FakeBus(_ok_reply())
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    types = [e["type"] for e in events]
    assert "queued" in types
    assert events[-1]["type"] == "done"