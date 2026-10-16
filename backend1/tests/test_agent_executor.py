# -*- coding: utf-8 -*-
"""session/agent_executor.py 全量用例：classify_error 分类、PartBridgeQueue
事件→part 落库 + SSE 转发 + 噪音丢弃。

运行：pytest tests/test_agent_executor.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.session.agent_executor as ae
from app.session.agent_executor import PartBridgeQueue, classify_error


# ── classify_error ─────────────────────────────────────────────────────────

def test_classify_ratelimit():
    r = classify_error(RuntimeError("rate limit exceeded"))
    assert r["retryable"] is True and r["status_code"] == 429
    r2 = classify_error(Exception("HTTP 429 Too Many Requests"))
    assert r2["retryable"] is True and r2["status_code"] == 429
    r3 = classify_error(Exception("too many requests"))
    assert r3["retryable"] is True


def test_classify_5xx():
    for s in ("502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout"):
        r = classify_error(Exception(s))
        assert r["retryable"] is True and r["status_code"] == 500
    r = classify_error(Exception("internal server error"))
    assert r["retryable"] is True and r["status_code"] == 500

    class InternalServerError(Exception):
        pass
    r2 = classify_error(InternalServerError("backend blew up"))
    assert r2["retryable"] is True and r2["status_code"] == 500
    assert r2["error_type"] == "internalservererror"  # 修复后 error_type 小写

    class RateLimitError(Exception):
        pass
    assert classify_error(RateLimitError("quota"))["retryable"] is True


def test_classify_timeout_and_connection():
    r = classify_error(TimeoutError("operation timed out"))
    assert r["retryable"] is True and r["status_code"] is None
    for s in ("connection refused", "connection error", "connection reset by peer"):
        assert classify_error(ConnectionError(s))["retryable"] is True
    # 仅含 connection 但无 error/refused/reset → 不判重试
    assert classify_error(ConnectionError("connection pool"))["retryable"] is False


def test_classify_overloaded_and_plain():
    r = classify_error(Exception("service_overloaded"))
    assert r["retryable"] is True and r["status_code"] == 503
    r2 = classify_error(ValueError("bad input"))
    assert r2["retryable"] is False and r2["status_code"] is None
    assert r2["error_type"] == "valueerror"  # 修复后 error_type 小写


# ── PartBridgeQueue 基建 ───────────────────────────────────────────────────

class Repo:
    def __init__(self):
        self.parts = []
        self.calls = []

    def append_part(self, session_id, message_id, part_type, data):
        self.parts.append((session_id, message_id, part_type, data))
        self.calls.append(("append", part_type))
        return SimpleNamespace(id=f"p{len(self.parts)}")

    def update_part(self, session_id, part_id, data):
        self.calls.append(("update", part_id, data))
        return SimpleNamespace(id=part_id)


@pytest.fixture
def repo(monkeypatch):
    r = Repo()
    monkeypatch.setattr(ae.repository, "append_part", r.append_part)
    monkeypatch.setattr(ae.repository, "update_part", r.update_part)
    return r


class InnerQ:
    def __init__(self):
        self.events = []

    def put_nowait(self, ev):
        self.events.append(ev)


def _bridge(repo, inner=None):
    return PartBridgeQueue(inner, "s1", "m1")


# ── put_nowait 事件映射 ────────────────────────────────────────────────────

def test_step_start_persists(repo):
    inner = InnerQ()
    b = _bridge(repo, inner)
    b.put_nowait({"type": "step_start", "step_id": "retrieve", "name": "检索中"})
    assert repo.parts[-1][2] == "step-start"
    assert repo.parts[-1][3]["state"] == "running"
    assert inner.events[-1]["part_id"] == "p1"


def test_step_end_persists(repo):
    b = _bridge(repo)
    b.put_nowait({"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": "d", "duration_ms": 5})
    assert repo.parts[-1][2] == "step-finish"
    assert repo.parts[-1][3]["state"] == "completed"


def test_tool_start_then_end_updates_same_part(repo):
    b = _bridge(repo)
    b.put_nowait({"type": "tool_start", "step_id": "tool_ls", "tool_name": "tool_ls", "tool_args": {"path": "/x"}})
    b.put_nowait({"type": "tool_end", "step_id": "tool_ls", "tool_name": "tool_ls", "status": "completed", "tool_result": "out"})
    assert repo.calls[0] == ("append", "tool")
    assert repo.calls[1][0] == "update"
    _, pid, data = repo.calls[1]
    assert pid == "p1"
    assert data["state"] == "completed"
    assert data["output"] == "out"
    assert data["args"] == {"path": "/x"}


def test_tool_key_includes_agent(repo):
    b = _bridge(repo)
    b.put_nowait({"type": "tool_start", "agent_id": "rag", "step_id": "tool_ls", "tool_name": "tool_ls"})
    b.put_nowait({"type": "tool_end", "agent_id": "rag", "step_id": "tool_ls", "tool_name": "tool_ls", "status": "completed"})
    assert repo.calls[1][1] == "p1"  # agent 前缀 key 命中


def test_tool_end_without_start_noop(repo):
    b = _bridge(repo)
    b.put_nowait({"type": "tool_end", "step_id": "tool_ls"})
    assert "update" not in [c[0] for c in repo.calls]


def test_text_delta_accumulates(repo):
    b = _bridge(repo)
    b.put_nowait({"type": "text_delta", "delta": "你好"})
    b.put_nowait({"type": "text_delta", "delta": "，世界"})
    assert repo.calls[0] == ("append", "text")
    assert repo.calls[0][0] == "append"
    updates = [c for c in repo.calls if c[0] == "update"]
    assert len(updates) == 1
    assert updates[0][2]["text"] == "你好，世界"


def test_put_nowait_persist_failure_tolerated(repo, caplog):
    def boom(session_id, message_id, part_type, data):
        raise RuntimeError("db down")
    repo.append_part = boom
    inner = InnerQ()
    b = _bridge(repo, inner)
    b.put_nowait({"type": "step_start", "step_id": "s"})  # 不抛，仍转发
    assert len(inner.events) == 1


def test_put_nowait_inner_failure_swallowed(repo):
    class BoomInner:
        def put_nowait(self, ev):
            raise RuntimeError("queue full")
    b = _bridge(repo, BoomInner())
    b.put_nowait({"type": "step_start", "step_id": "s"})  # 不抛


# ── append_text / replay / append_agent ────────────────────────────────────

def test_append_text_new_part(repo):
    b = _bridge(repo)
    b.append_text("final answer")
    assert repo.calls == [("append", "text")]
    assert repo.parts[-1][3]["text"] == "final answer"


def test_append_text_updates_existing(repo):
    b = _bridge(repo)
    b.put_nowait({"type": "text_delta", "delta": "partial"})
    b.append_text("authoritative")
    assert repo.calls[0] == ("append", "text")
    assert repo.calls[1][0] == "update"
    assert repo.calls[1][2]["text"] == "authoritative"


def test_append_text_empty_noop(repo):
    b = _bridge(repo)
    b.append_text("")
    assert repo.calls == []


def test_replay_agent_steps(repo):
    b = _bridge(repo)
    steps = [
        {"type": "step_start", "step_id": "x", "name": "n"},
        "not-a-dict",
        {"type": "tool_end", "step_id": "y"},
    ]
    b.replay_agent_steps(steps, agent_id="rag")
    assert len(repo.parts) == 1  # 非 dict 跳过；tool_end 无对应 tool_start → 无 part
    # [C5 修复] step part 现在也落 agent_id（多 Agent 历史按 agent 归档）
    assert repo.parts[0][3].get("agent_id") == "rag"


def test_replay_agent_steps_persist_failure(repo, caplog):
    def boom(session_id, message_id, part_type, data):
        raise RuntimeError("boom")
    repo.append_part = boom
    b = _bridge(repo)
    b.replay_agent_steps([{"type": "step_start", "step_id": "x"}])  # 不抛


def test_append_agent(repo):
    b = _bridge(repo)
    b.append_agent({"agent_id": "rag", "agent_name": "知识库", "status": "completed"})
    assert repo.parts[-1][2] == "agent"
    assert repo.parts[-1][3]["agent_id"] == "rag"
    # 无 name → 回退 agent_id
    b.append_agent({"agent_id": "web_search", "status": "failed"})
    assert repo.parts[-1][3]["agent_name"] == "web_search"


def test_append_agent_failure_tolerated(repo, caplog):
    def boom(session_id, message_id, part_type, data):
        raise RuntimeError("boom")
    repo.append_part = boom
    b = _bridge(repo)
    b.append_agent({"agent_id": "rag"})  # 不抛