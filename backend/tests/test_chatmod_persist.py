# -*- coding: utf-8 -*-
"""chatmod/persist.py 全量用例：多 Agent 会话解析、历史投影/压缩、子任务会话、
幂等去重（B4）、部分结果落库（B11）、子会话故障容忍（B13）。

运行：pytest tests/test_chatmod_persist.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi import HTTPException

import app.api.chatmod.persist as p
from app.session.repository import SessionNotFound


class Msg:
    def __init__(self, id, type="user", data=None):
        self.id = id
        self.type = type
        self.data = data or {}


class MockService:
    def __init__(self):
        self.sessions = {}
        self._msgs = {}
        self.creates = []
        self.updates = []

    def write_lock(self, session_id):
        return self  # 复用自身作为 async CM

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def create(self, user_id, **kw):
        self.creates.append(kw)
        sid = kw.get("session_id") or f"s{len(self.creates)}"
        self.sessions[sid] = SimpleNamespace(id=sid, directory=kw.get("directory", ""))
        return self.sessions[sid]

    def get(self, user_id, session_id):
        if session_id not in self.sessions:
            raise SessionNotFound(session_id)
        return self.sessions[session_id]

    def append_message(self, user_id, session_id, msg_type, data):
        mid = f"m{len(self._msgs.get(session_id, [])) + 1}"
        m = Msg(mid, msg_type, data)
        self._msgs.setdefault(session_id, []).append(m)
        return m

    def messages(self, user_id, session_id, limit=None):
        msgs = self._msgs.get(session_id, [])
        return msgs[-limit:] if limit else msgs

    def update(self, user_id, session_id, **fields):
        self.updates.append((session_id, fields))
        return self.sessions.get(session_id)


@pytest.fixture
def service(monkeypatch):
    svc = MockService()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_service=svc)))
    return svc, req


# ── _resolve_multi_agent_parent ────────────────────────────────────────────

def test_resolve_parent_existing(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="conv1", directory="/dir")
    svc.sessions["conv1"].directory = "/wd"
    out = p._resolve_multi_agent_parent(req, "u1", "conv1")
    assert out == (svc, "conv1", "/wd")


def test_resolve_parent_not_found_404(service):
    svc, req = service
    with pytest.raises(HTTPException) as e:
        p._resolve_multi_agent_parent(req, "u1", "ghost")
    assert e.value.status_code == 404


def test_resolve_parent_creates_new(service, monkeypatch):
    svc, req = service
    monkeypatch.setattr(p, "discover_project_root", lambda: "/root")
    out = p._resolve_multi_agent_parent(req, "u1", None, directory="")
    assert out[1] == "s1"
    assert out[2] == "/root"
    assert svc.creates[0]["kind"] == "multi-agent"
    assert svc.creates[0]["directory"] == "/root"


def test_resolve_parent_creates_with_directory(service, monkeypatch):
    svc, req = service
    out = p._resolve_multi_agent_parent(req, "u1", None, directory="/custom")
    assert svc.creates[0]["directory"] == "/custom"


# ── _session_history_for ───────────────────────────────────────────────────

def test_session_history_text_from_parts(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "assistant", {"role": "assistant", "content": "fallback"})
    monkeypatch.setattr(p.session_repo, "list_parts_for_messages",
                        lambda ids: {ids[0]: [SimpleNamespace(type="text", data={"text": "PART_TEXT"})]} if ids else {})
    hist = p._session_history_for(svc, "u1", "s1")
    assert hist == [{"role": "assistant", "content": "PART_TEXT"}]


def test_session_history_fallback_content(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "assistant", {"role": "assistant", "content": "from data"})
    monkeypatch.setattr(p.session_repo, "list_parts_for_messages", lambda ids: {})
    hist = p._session_history_for(svc, "u1", "s1")
    assert hist == [{"role": "assistant", "content": "from data"}]


def test_session_history_skips_tool_and_unmapped(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "tool", {"role": "tool", "content": "out"})
    monkeypatch.setattr(p.session_repo, "list_parts_for_messages", lambda ids: {})
    assert p._session_history_for(svc, "u1", "s1") == []


# ── _build_compressed_history ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_compressed_with_summarizer(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"role": "user", "content": "hi"})

    class FakeSummarizer:
        async def apply(self, history):
            return [{"role": "system", "content": "压缩"}]
    monkeypatch.setattr(p, "_get_summarizer", lambda: FakeSummarizer())
    out = await p._build_compressed_history(svc, "u1", "s1")
    assert out == [{"role": "system", "content": "压缩"}]


@pytest.mark.asyncio
async def test_build_compressed_without_summarizer(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"role": "user", "content": "hi"})
    monkeypatch.setattr(p, "_get_summarizer", lambda: None)
    out = await p._build_compressed_history(svc, "u1", "s1")
    assert out == [{"role": "user", "content": "hi"}]


# ── _begin_task_session ────────────────────────────────────────────────────

def test_begin_task_session(service, monkeypatch):
    svc, req = service
    reg = []
    monkeypatch.setattr(p.task_bridge, "register", lambda cid, tid: reg.append((cid, tid)))
    child_id, thread_id = p._begin_task_session(svc, "u1", "parent1", "  问题内容  ")
    assert child_id == "s1"
    assert thread_id.startswith("parent1:task:")
    assert svc.creates[0]["kind"] == "task"
    assert svc.creates[0]["agent"] == "supervisor"
    assert reg[0][0] == "s1" and reg[0][1] == thread_id


# ── _existing_pair ─────────────────────────────────────────────────────────

def test_existing_pair_no_client_msg_id(service):
    svc, req = service
    svc.create("u1", session_id="s1")
    assert p._existing_pair(svc, "u1", "s1", None) == (None, None)


def test_existing_pair_found(service):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "c1"})
    svc.append_message("u1", "s1", "assistant", {})
    assert p._existing_pair(svc, "u1", "s1", "c1") == ("m1", "m2")


def test_existing_pair_interrupted_assistant(service):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "c1"})
    svc.append_message("u1", "s1", "assistant", {"interrupted": True})
    assert p._existing_pair(svc, "u1", "s1", "c1") == ("m1", None)


def test_existing_pair_user_only(service):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "c1"})
    assert p._existing_pair(svc, "u1", "s1", "c1") == ("m1", None)


def test_existing_pair_not_found(service):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "other"})
    assert p._existing_pair(svc, "u1", "s1", "c1") == (None, None)


# ── _ensure_child_pair ─────────────────────────────────────────────────────

def test_ensure_child_pair_existing_assistant(service):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "c1"})
    svc.append_message("u1", "s1", "assistant", {})
    before = len(svc._msgs["s1"])
    p._ensure_child_pair(svc, "u1", "s1", "q", "a", [], [], None, None, None, "c1")
    assert len(svc._msgs["s1"]) == before  # 幂等，不重复写


def test_ensure_child_pair_creates(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    p._ensure_child_pair(svc, "u1", "s1", "q", "a", [], [], None, None, None, "c1")
    assert [m.type for m in svc._msgs["s1"]] == ["user", "assistant"]
    assert svc._msgs["s1"][0].data["client_msg_id"] == "c1"
    assert svc.updates and svc.updates[0][1].get("status") == "idle"


# ── _persist_multi_agent ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_multi_agent_full(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.create("u1", session_id="c1")
    monkeypatch.setattr(p.session_repo, "latest_seq", lambda sid: 1)
    monkeypatch.setattr(p, "_persist_multi_agent_parts", lambda *a, **k: None)
    u, a = await p._persist_multi_agent(svc, "u1", "s1", "c1", "q", "A", [], [],
                                        agents=[{"agent_id": "rag"}], model="m", tokens={"i": 1},
                                        client_msg_id="c1")
    assert u == "m1" and a == "m2"
    assert svc._msgs["s1"][0].type == "user"
    assert svc._msgs["s1"][1].data["role"] == "assistant"
    # 新会话（seq==1）→ 更新标题
    assert svc.updates[0][1].get("title") == "q"
    # 子会话已写 user/assistant
    assert [m.type for m in svc._msgs["c1"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_persist_multi_agent_idempotent_reuse(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.create("u1", session_id="c1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "c1"})
    svc.append_message("u1", "s1", "assistant", {})
    monkeypatch.setattr(p, "_persist_multi_agent_parts", lambda *a, **k: None)
    u, a = await p._persist_multi_agent(svc, "u1", "s1", "c1", "q", "A", [], [],
                                        client_msg_id="c1")
    assert (u, a) == ("m1", "m2")  # 复用已落库 id
    assert len(svc._msgs["s1"]) == 2  # 未重复写入主会话
    assert len(svc._msgs["c1"]) == 2  # 子会话幂等


@pytest.mark.asyncio
async def test_persist_multi_agent_child_failure_tolerated(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.create("u1", session_id="c1")
    monkeypatch.setattr(p.session_repo, "latest_seq", lambda sid: 1)
    monkeypatch.setattr(p, "_persist_multi_agent_parts", lambda *a, **k: None)

    def maybe_boom(user_id, session_id, **fields):
        if session_id == "c1":
            raise RuntimeError("child db down")
        return svc.sessions.get(session_id)
    svc.update = maybe_boom
    # 子会话失败不中断主会话（B13）
    u, a = await p._persist_multi_agent(svc, "u1", "s1", "c1", "q", "A", [], [], client_msg_id=None)
    assert a is not None


# ── _persist_interrupted_partial ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_interrupted_partial(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.create("u1", session_id="c1")
    monkeypatch.setattr(p, "_persist_multi_agent_parts", lambda *a, **k: None)
    await p._persist_interrupted_partial(svc, "u1", "s1", "c1", "q", "部分答案",
                                         [{"agent_id": "rag", "content": "x"}], "c1")
    # 主/子会话各 user+assistant（interrupted=True）
    assert len(svc._msgs["s1"]) == 2
    assert svc._msgs["s1"][1].data.get("interrupted") is True
    assert len(svc._msgs["c1"]) == 2
    # 子会话 status=interrupted
    assert svc.updates and svc.updates[0][1].get("status") == "interrupted"


@pytest.mark.asyncio
async def test_persist_interrupted_partial_existing(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.create("u1", session_id="c1")
    svc.append_message("u1", "s1", "user", {"client_msg_id": "c1"})
    svc.append_message("u1", "s1", "assistant", {})
    monkeypatch.setattr(p, "_persist_multi_agent_parts", lambda *a, **k: None)
    await p._persist_interrupted_partial(svc, "u1", "s1", "c1", "q", "部分", [], "c1")
    # 完整轮次已存在 → 复用，不重复写主会话
    assert len(svc._msgs["s1"]) == 2


@pytest.mark.asyncio
async def test_persist_interrupted_partial_failure_tolerated(service, monkeypatch):
    svc, req = service
    svc.create("u1", session_id="s1")
    svc.create("u1", session_id="c1")
    monkeypatch.setattr(p, "_persist_multi_agent_parts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    await p._persist_interrupted_partial(svc, "u1", "s1", "c1", "q", "部分", [], "c1")  # 不抛