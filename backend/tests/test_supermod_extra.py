# -*- coding: utf-8 -*-
"""supermod base/core/decompose/parallel 剩余分支用例（mock LLM/bus）。

覆盖：
  - base：_timeout_for 分级、_start_heartbeat 心跳 touch 与取消
  - core：handle_message 全分支（非 request/未知动作/单子任务路由/多子任务并行/
    白名单过滤回退 rag/心跳收尾）、_route_to（response/error/unexpected/超时/异常）
  - decompose：_decompose 关键词路由/多意图 LLM/寒暄/短问题、_llm_decompose
    （合法/非法重试/双失败回退 rag/用量汇总）
  - parallel：_execute_parallel（单成功/多成功/error/超时/异常/用量汇总）、
    _synthesize（截断/失败回退）
运行：pytest tests/test_supermod_extra.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.agent.supermod.decompose as dec
import app.agent.supermod.parallel as par
from app.agent.base import AgentMessage
from app.agent.bus import AgentBus
from app.agent.supermod.parallel import SupervisorAgent
from app.config import settings


class FakeBus:
    def __init__(self, agents=("rag", "code", "web_search"), reply=None):
        self._agents = list(agents)
        self.reply = reply
        self.touched = []
        self.calls = []

    def list_agents(self):
        return list(self._agents)

    def touch(self, aid, progress=""):
        self.touched.append(aid)

    def agent_progress(self, aid):
        return ["步骤1"]

    async def send_and_wait(self, msg, timeout=None):
        self.calls.append((msg, timeout))
        return self.reply


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(settings, "extended_timeout_agents", "code")
    return SupervisorAgent(FakeBus())


def _msg(action="chat", payload=None, type="request", thread="t1"):
    return AgentMessage(source="user", target="supervisor", type=type,
                        action=action, payload=payload or {}, thread_id=thread)


async def _collect(agen, msg):
    return [r async for r in agen.handle_message(msg)]


# ── base ───────────────────────────────────────────────────────────────────

def test_timeout_for(agent, monkeypatch):
    monkeypatch.setattr(settings, "sub_agent_timeout", 150.0)
    monkeypatch.setattr(settings, "sub_agent_timeout_extended", 300.0)
    assert agent._timeout_for("code") == 300.0
    assert agent._timeout_for("rag") == 150.0


@pytest.mark.asyncio
async def test_heartbeat_touches_and_cancels(agent):
    beat = agent._start_heartbeat(interval=0.01)
    await asyncio.sleep(0.05)
    assert agent._bus.touched  # 心跳已 touch
    beat.cancel()
    await asyncio.sleep(0.01)


# ── decompose ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decompose_keyword_single(agent, monkeypatch):
    monkeypatch.setattr(agent, "_llm_decompose", lambda q, a: (_ for _ in ()).throw(AssertionError("不应调用 LLM")))
    assert await agent._decompose("帮我找文档里的情节") == [{"agent": "rag", "question": "帮我找文档里的情节"}]
    assert await agent._decompose("写一个 python 函数") == [{"agent": "code", "question": "写一个 python 函数"}]
    assert await agent._decompose("查一下今天新闻") == [{"agent": "web_search", "question": "查一下今天新闻"}]


@pytest.mark.asyncio
async def test_decompose_multi_intent_uses_llm(agent, monkeypatch):
    called = []

    async def fake_llm(q, a):
        called.append((q, a))
        return [{"agent": "code", "question": q}]
    monkeypatch.setattr(agent, "_llm_decompose", fake_llm)
    out = await agent._decompose("帮我写代码并搜索新闻")
    assert called  # 多意图 → LLM
    assert out[0]["agent"] == "code"


@pytest.mark.asyncio
async def test_decompose_greeting_short(agent, monkeypatch):
    monkeypatch.setattr(agent, "_llm_decompose", lambda q, a: (_ for _ in ()).throw(AssertionError("不应调用 LLM")))
    assert await agent._decompose("你好") == [{"agent": "rag", "question": "你好"}]
    assert agent._is_greeting("你好呀") is True


@pytest.mark.asyncio
async def test_decompose_short_non_greeting_uses_llm(agent, monkeypatch):
    called = []

    async def fake_llm(q, a):
        called.append(q)
        return [{"agent": "rag", "question": q}]
    monkeypatch.setattr(agent, "_llm_decompose", fake_llm)
    await agent._decompose("写个爬虫")
    assert called


@pytest.mark.asyncio
async def test_llm_decompose_valid(agent, monkeypatch):
    monkeypatch.setattr(settings, "extended_timeout_agents", "code")
    async def fake_acompletion(**kw):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='[{"agent": "rag", "question": "q1"}, {"agent": "web_search", "question": "q2"}]'))],
        )
    monkeypatch.setattr(dec.litellm, "acompletion", fake_acompletion)
    agent._usage = {"input": 0, "output": 0}
    out = await agent._llm_decompose("问题", ["rag", "web_search", "code"])
    assert [s["agent"] for s in out] == ["rag", "web_search"]
    assert agent._usage["input"] == 10 and agent._usage["output"] == 5


@pytest.mark.asyncio
async def test_llm_decompose_retry_then_valid(agent, monkeypatch):
    monkeypatch.setattr(settings, "extended_timeout_agents", "code")
    responses = [
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]),
        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                        choices=[SimpleNamespace(message=SimpleNamespace(content='[{"agent": "rag", "question": "ok"}]'))]),
    ]
    async def fake_acompletion(**kw):
        return responses.pop(0)
    monkeypatch.setattr(dec.litellm, "acompletion", fake_acompletion)
    agent._usage = {"input": 0, "output": 0}
    out = await agent._llm_decompose("问题", ["rag"])
    assert out == [{"agent": "rag", "question": "ok"}]


@pytest.mark.asyncio
async def test_llm_decompose_fallback_rag(agent, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(dec.litellm, "acompletion", boom)
    agent._usage = {"input": 0, "output": 0}
    out = await agent._llm_decompose("问题", [])
    assert out == [{"agent": "rag", "question": "问题"}]


def test_validate_subtasks():
    assert SupervisorAgent._validate_subtasks("nope", ["rag"]) == []
    assert SupervisorAgent._validate_subtasks([{"agent": "supervisor", "question": "x"}], ["rag"]) == []
    assert SupervisorAgent._validate_subtasks([
        {"agent": "rag", "question": "  q1  "},
        {"agent": "evil", "question": "q2"},
        "not-dict",
        {"agent": "web_search", "question": "q3"},
        {"agent": "code", "question": "q4"},
    ], ["rag", "web_search", "code"]) == [
        {"agent": "rag", "question": "q1"},
        {"agent": "web_search", "question": "q3"},
        {"agent": "code", "question": "q4"},
    ]  # 白名单过滤 + 最多 3 个


# ── core handle_message ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_non_request(agent):
    assert await _collect(agent, _msg(type="response")) == []


@pytest.mark.asyncio
async def test_handle_unknown_action(agent):
    replies = await _collect(agent, _msg(action="bogus"))
    assert replies[0].type == "error"
    assert "doesn't support action" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_handle_single_route(agent, monkeypatch):
    async def fake_route(target, payload, tid):
        yield AgentMessage(source="supervisor", target="user", type="response", action="chat",
                           payload={"answer": "A", "routed_to": target}, thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "code", "question": q}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)
    replies = await _collect(agent, _msg(payload={"question": "q"}))
    assert replies[0].payload["routed_to"] == "code"


@pytest.mark.asyncio
async def test_handle_multi_parallel(agent, monkeypatch):
    async def fake_parallel(subtasks, payload, tid):
        return AgentMessage(source="supervisor", target="user", type="response", action="chat",
                            payload={"answer": "P", "routed_to": "rag+code"}, thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "rag", "question": "a"}, {"agent": "code", "question": "b"}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_execute_parallel", fake_parallel)
    replies = await _collect(agent, _msg(payload={"question": "q"}))
    assert replies[0].payload["routed_to"] == "rag+code"


@pytest.mark.asyncio
async def test_handle_filters_non_routable(agent, monkeypatch):
    """LLM 返回 supervisor → 白名单过滤 → 回退 rag。"""
    async def fake_route(target, payload, tid):
        assert target == "rag"
        yield AgentMessage(source="supervisor", target="user", type="response", action="chat",
                           payload={"answer": "R", "routed_to": target}, thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "supervisor", "question": "x"}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)
    replies = await _collect(agent, _msg(payload={"question": "q"}))
    assert replies[0].payload["routed_to"] == "rag"


# ── _route_to ──────────────────────────────────────────────────────────────

def _reply(type="response", payload=None):
    return AgentMessage(source="sub", target="supervisor", type=type, action="chat",
                        payload=payload or {}, thread_id="sub")


@pytest.mark.asyncio
async def test_route_to_response(agent):
    agent._bus.reply = _reply(payload={"answer": "A", "tokens": {"input": 3}})
    agent._usage = {"input": 0, "output": 0}
    replies = [r async for r in agent._route_to("rag", {"question": "q"}, "t1")]
    assert replies[0].payload["routed_to"] == "rag"
    assert replies[0].payload["answer"] == "A"
    assert agent._usage["input"] == 3


@pytest.mark.asyncio
async def test_route_to_error(agent):
    agent._bus.reply = _reply(type="error", payload={"error": "boom", "error_type": "sub_agent_error", "completed_steps": []})
    replies = [r async for r in agent._route_to("rag", {}, "t1")]
    assert replies[0].type == "error"
    assert "boom" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_route_to_unexpected_type(agent):
    agent._bus.reply = _reply(type="weird")
    replies = [r async for r in agent._route_to("rag", {}, "t1")]
    assert "unexpected type" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_route_to_timeout(agent):
    async def boom(msg, timeout=None):
        raise asyncio.TimeoutError()
    agent._bus.send_and_wait = boom
    replies = [r async for r in agent._route_to("rag", {}, "t1")]
    assert replies[0].type == "error"
    assert replies[0].payload["error_type"] == "sub_agent_timeout"
    assert "已完成步骤: 步骤1" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_route_to_exception(agent):
    async def boom(msg, timeout=None):
        raise RuntimeError("bus dead")
    agent._bus.send_and_wait = boom
    replies = [r async for r in agent._route_to("rag", {}, "t1")]
    assert replies[0].payload["error_type"] == "sub_agent_error"


# ── parallel ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_parallel_single(agent):
    agent._bus.reply = _reply(payload={"answer": "A", "tokens": {"input": 1}})
    agent._usage = {"input": 0, "output": 0}
    msg = await agent._execute_parallel([{"agent": "rag", "question": "q"}], {"question": "q"}, "t1")
    assert msg.payload["routed_to"] == "rag"
    assert msg.payload["answer"] == "A"


@pytest.mark.asyncio
async def test_execute_parallel_multi_synthesize(agent, monkeypatch):
    async def fake_synth(question, results):
        return "合成"
    monkeypatch.setattr(agent, "_synthesize", fake_synth)
    agent._bus.reply = _reply(payload={"answer": "X", "sources": [{"document_id": "d"}]})
    agent._usage = {"input": 0, "output": 0}
    msg = await agent._execute_parallel(
        [{"agent": "rag", "question": "a"}, {"agent": "web_search", "question": "b"}],
        {"question": "q"}, "t1",
    )
    assert msg.payload["routed_to"] == "rag+web_search"
    assert "合成" in msg.payload["answer"]


@pytest.mark.asyncio
async def test_execute_parallel_error_and_timeout(agent):
    replies = iter([
        _reply(type="error", payload={"error": "sub failed", "completed_steps": ["s1"]}),
    ])
    async def fake_send(msg, timeout=None):
        return next(replies)
    agent._bus.send_and_wait = fake_send
    agent._usage = {"input": 0, "output": 0}
    msg = await agent._execute_parallel([{"agent": "rag", "question": "a"}], {}, "t1")
    assert "部分 Agent 执行出错" in msg.payload["answer"]
    assert "[rag] sub failed" in msg.payload["answer"]


@pytest.mark.asyncio
async def test_execute_parallel_timeout_error(agent):
    async def boom(msg, timeout=None):
        raise asyncio.TimeoutError()
    agent._bus.send_and_wait = boom
    agent._usage = {"input": 0, "output": 0}
    msg = await agent._execute_parallel([{"agent": "code", "question": "a"}], {}, "t1")
    assert "did not respond in time" in msg.payload["answer"]
    # 已完成步骤随错误回传（suggestion 字段保留在结果 dict 中，未进汇总文案）
    assert "已完成: 步骤1" in msg.payload["answer"]


@pytest.mark.asyncio
async def test_synthesize_empty_and_truncation(agent, monkeypatch):
    assert await agent._synthesize("q", []) == "抱歉，所有 Agent 都未能返回结果。"
    # 超长截断（mock LLM 避免真实调用；截断发生在发给 LLM 的 prompt 中）
    seen = {}

    async def fake_acompletion(**kw):
        seen["messages"] = kw["messages"]
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            choices=[SimpleNamespace(message=SimpleNamespace(content="合成"))],
        )
    monkeypatch.setattr(par.litellm, "acompletion", fake_acompletion)
    r = {"agent": "rag", "original_question": "x", "answer": "y" * 5000}
    out = await agent._synthesize("q", [r])
    assert "已截断" in seen["messages"][1]["content"]
    assert out == "合成"


@pytest.mark.asyncio
async def test_synthesize_llm_and_fallback(agent, monkeypatch):
    async def fake_acompletion(**kw):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
            choices=[SimpleNamespace(message=SimpleNamespace(content="  合成结果  "))],
        )
    monkeypatch.setattr(par.litellm, "acompletion", fake_acompletion)
    agent._usage = {"input": 0, "output": 0}
    out = await agent._synthesize("q", [{"agent": "rag", "original_question": "a", "answer": "ans"}])
    assert out == "合成结果"
    # 失败回退
    async def boom(**kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(par.litellm, "acompletion", boom)
    out2 = await agent._synthesize("q", [{"agent": "rag", "original_question": "a", "answer": "ans"}])
    assert "以下是多个来源的信息汇总" in out2