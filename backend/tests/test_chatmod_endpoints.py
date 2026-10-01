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
    """mock endpoints 依赖：resolve/build/persist/begin。

    队列：svc.prompts 是「pop_prompt 依次返回」的脚本（默认空 = 队列空），
    记录 pop 调用次数用于断言「回合末尾原子 pop」而非 count-then-act。
    """
    async def _pop(session_id):
        pops.append(session_id)
        return prompts.pop(0) if prompts else None

    svc = SimpleNamespace(update=lambda *a, **k: None, pop_prompt=_pop)
    prompts: list = []          # 被 pop 出来的排队任务（按 pop 顺序）
    pops: list = []             # pop_prompt 调用轨迹
    svc._queue = prompts
    svc._pops = pops
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


# ── [队列竞态] 回合末尾原子 pop：done/turn_done 的判定必须发生在回合结束时 ────

def _queued(pid: str, message: str) -> dict:
    return {"id": pid, "session_id": "s1", "prompt": {"message": message}}


@pytest.mark.asyncio
async def test_stream_drains_queued_prompts_before_done(env, monkeypatch):
    """有排队任务 → 中间发 turn_done 继续跑，最后一个回合才发 done。"""
    env._queue.extend([_queued("p1", "第二个"), _queued("p2", "第三个")])
    sent: list[str] = []

    async def fake_send_and_wait(msg, *a, **k):
        sent.append(msg.payload.get("content") or msg.payload.get("message") or "")
        return _ok_reply()

    bus = FakeBus(_ok_reply())
    monkeypatch.setattr(bus, "send_and_wait", fake_send_and_wait)
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))

    types = [e["type"] for e in events]
    assert types.count("turn_done") == 2, types
    assert types[-1] == "done"
    # turn_done 带回「下一条排队任务 id」，便于前端追踪 drain 进度
    tds = [e for e in events if e["type"] == "turn_done"]
    assert [t["next_prompt_id"] for t in tds] == ["p1", "p2"]
    assert all(t["queued_turn"] for t in tds)
    assert len(sent) == 3


@pytest.mark.asyncio
async def test_stream_pop_once_per_turn_not_count_then_act(env, monkeypatch):
    """回归：回合开始前 count、回合结束后再 pop 会漏掉期间入队的任务。
    现在每回合只在**结束时** pop 一次（原子认领），不做 count-then-act。"""
    bus = FakeBus(_ok_reply())
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    _parse_sse(await _drain_stream(resp))
    # 一个回合 → 恰好一次 pop（原来这里会先 count 再 pop，count 结果被丢弃）
    assert len(env._pops) == 1


@pytest.mark.asyncio
async def test_stream_prompt_enqueued_during_turn_is_drained(env, monkeypatch):
    """执行期间入队的任务：本轮结束后仍会被 drain 出来（而不是静默留在队列里）。"""
    bus = FakeBus(_ok_reply())
    original = bus.send_and_wait
    state = {"n": 0}

    async def send_and_wait(msg, *a, **k):
        state["n"] += 1
        if state["n"] == 1:                      # 第一轮跑的过程中用户又追加了任务
            env._queue.append(_queued("late", "期间追加"))
        return await original(msg, *a, **k)

    monkeypatch.setattr(bus, "send_and_wait", send_and_wait)
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    types = [e["type"] for e in events]
    assert types.count("turn_done") == 1 and types[-1] == "done"
    assert env._pops == ["s1", "s1"]


@pytest.mark.asyncio
async def test_stream_turn_error_leaves_queue_untouched(env, monkeypatch):
    """回合报错时不认领排队任务（留给下一轮用户消息），避免错误态下丢任务。"""
    env._queue.append(_queued("p1", "排队的"))
    bus = FakeBus(AgentMessage(source="supervisor", target="user", type="error", action="chat",
                               payload={"error": "boom"}))
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    assert events[-1]["type"] == "error"
    assert env._pops == []                      # 没被 pop 走
    assert len(env._queue) == 1                 # 仍在队列里


# ── [D3] model_switched 事件字段契约 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_model_switched_uses_model_ref_key(env, monkeypatch):
    """前端 store 读 `event.model_ref`；后端必须发同名键（曾误发 `model`）。"""
    bus = FakeBus(_ok_reply())
    monkeypatch.setattr(ep, "_sync_session_model", lambda *a, **k: True)
    monkeypatch.setattr(ep, "model_ref_dict", lambda m: {"id": m, "provider": "p", "name": "N"})

    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body(model="deepseek/deepseek-v4-flash"))
    events = _parse_sse(await _drain_stream(resp))

    switched = [e for e in events if e["type"] == "model_switched"]
    assert len(switched) == 1, "切模型时应发一次 model_switched"
    assert switched[0]["model_ref"] == {
        "id": "deepseek/deepseek-v4-flash", "provider": "p", "name": "N",
    }
    assert "model" not in switched[0], "旧键 model 会让前端读不到（契约漂移的根因）"


@pytest.mark.asyncio
async def test_stream_no_model_switched_when_model_unchanged(env, monkeypatch):
    bus = FakeBus(_ok_reply())
    monkeypatch.setattr(ep, "_sync_session_model", lambda *a, **k: False)
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body(model="x/y"))
    events = _parse_sse(await _drain_stream(resp))
    assert not [e for e in events if e["type"] == "model_switched"]


# ── [C1] plan→build 失败：计划正文必须保留，不得被静默丢弃 ──────────────────

def _partial_error_reply():
    """模拟 supervisor 合并 plan→build 后 build 失败：payload 里带着完整计划。"""
    return AgentMessage(
        source="supervisor", target="user", type="error", action="chat",
        payload={
            "error": "执行失败", "error_type": "sub_agent_error",
            "answer": "## 实施计划\n\n步骤一\n\n## 执行结果（出错）\n执行失败",
            "routed_to": "plan→build", "plan_path": "/data/plans/s1/plan.md",
            "sources": [], "steps": [],
        },
    )


@pytest.mark.asyncio
async def test_chat_multi_agent_reply_error_with_plan_keeps_plan(env, monkeypatch):
    """错误但带部分答案（计划已成）→ 走正常返回路径，不能抛 500 把计划丢掉。"""
    bus = FakeBus(_partial_error_reply())
    resp = await ep.chat_multi_agent(_req(bus, object()), _body())
    assert "## 实施计划" in resp.answer
    assert "步骤一" in resp.answer
    assert resp.routed_to == "plan→build"
    assert resp.plan_path == "/data/plans/s1/plan.md"


@pytest.mark.asyncio
async def test_stream_partial_error_emits_done_with_plan(env, monkeypatch):
    """流式：带计划的错误走 done + partial_error（走 error 事件会覆盖正文）。"""
    bus = FakeBus(_partial_error_reply())
    resp = await ep.chat_multi_agent_stream(_req(bus, object()), _body())
    events = _parse_sse(await _drain_stream(resp))
    assert events[-1]["type"] == "done", "带计划的错误不应退化成 error 事件"
    assert "## 实施计划" in events[-1]["answer"]
    assert events[-1]["partial_error"] == "执行失败"
    assert events[-1]["plan_path"] == "/data/plans/s1/plan.md"
    # 正文必须真的落库，刷新后不丢
    assert events[-1]["assistant_msg_id"] == "am1"


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