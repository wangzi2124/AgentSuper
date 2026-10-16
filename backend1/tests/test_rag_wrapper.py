# -*- coding: utf-8 -*-
"""rag_wrapper.py 全量用例：BaseAgent 适配器 chat/retrieve/generate/未知动作/
异常兜底/心跳/非 request。

运行：pytest tests/test_rag_wrapper.py
"""
import asyncio
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent.base import AgentMessage
from app.agent.rag_wrapper import RAGAgentWrapper


class FakeInner:
    def __init__(self, answer="A", sources=None):
        self.answer = answer
        self.sources = sources or []
        self.calls = []

    async def invoke(self, **kw):
        self.calls.append(kw)
        return {"answer": self.answer, "sources": self.sources, "steps": [{"step_id": "s"}], "tokens": {"input": 9}}


def _msg(action="chat", payload=None, type="request", thread="t1"):
    return AgentMessage(source="user", target="rag", type=type,
                        action=action, payload=payload or {}, thread_id=thread)


def _wrap(inner=None, heartbeat=None):
    return RAGAgentWrapper(inner or FakeInner(), heartbeat=heartbeat)


async def _collect(w, msg):
    return [r async for r in w.handle_message(msg)]


@pytest.mark.asyncio
async def test_non_request_returns_nothing():
    w = _wrap()
    assert await _collect(w, _msg(type="response")) == []


@pytest.mark.asyncio
async def test_chat_action():
    inner = FakeInner()
    w = _wrap(inner)
    q = asyncio.Queue()
    replies = await _collect(w, _msg(payload={"question": "q", "conversation_id": "cid", "_event_queue": q}))
    assert replies[0].type == "response"
    assert replies[0].payload["answer"] == "A"
    assert replies[0].payload["tokens"] == {"input": 9}
    kw = inner.calls[0]
    assert kw["question"] == "q"
    assert kw["conversation_id"] == "cid"
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "agent_start" in types and "agent_done" in types


@pytest.mark.asyncio
async def test_retrieve_action():
    inner = FakeInner(sources=[{"document_id": "d1"}])
    w = _wrap(inner)
    replies = await _collect(w, _msg(action="retrieve", payload={"query": "x"}))
    assert replies[0].action == "retrieve"
    assert replies[0].payload["sources"] == [{"document_id": "d1"}]
    assert inner.calls[0]["use_vector_db"] is True


@pytest.mark.asyncio
async def test_generate_action():
    inner = FakeInner()
    w = _wrap(inner)
    replies = await _collect(w, _msg(action="generate", payload={"prompt": "p"}))
    assert replies[0].action == "generate"
    assert inner.calls[0]["use_vector_db"] is False


@pytest.mark.asyncio
async def test_unknown_action():
    w = _wrap()
    replies = await _collect(w, _msg(action="bogus"))
    assert replies[0].type == "error"
    assert "Unknown action" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_exception_handled():
    class BoomInner:
        async def invoke(self, **kw):
            raise RuntimeError("inner boom")
    w = _wrap(BoomInner())
    q = asyncio.Queue()
    replies = await _collect(w, _msg(payload={"_event_queue": q}))
    assert replies[0].type == "error"
    assert "inner boom" in replies[0].payload["error"]
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "agent_error" in types


def test_notify_heartbeat():
    calls = []
    w = _wrap(heartbeat=lambda aid, prog: calls.append((aid, prog)))
    w._notify("进度")
    assert calls == [("rag", "进度")]


def test_notify_heartbeat_error_swallowed():
    def boom(aid, prog):
        raise RuntimeError
    w = _wrap(heartbeat=boom)
    w._notify("进度")  # 不抛