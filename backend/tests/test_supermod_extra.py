# -*- coding: utf-8 -*-
"""supermod base/core/decompose 剩余分支用例（mock LLM/bus）。

[C4] `parallel` 切片已删除（并行分解恒不可达），相应用例一并移除；
    `_llm_decompose` / `_validate_subtasks` 亦已删除，故不再 monkeypatch 它们。

覆盖：
  - base：_timeout_for 分级、_start_heartbeat 心跳 touch 与取消
  - core：handle_message 全分支（非 request/未知动作/单子任务路由/
    白名单过滤回退 build/心跳收尾）、_route_to（response/error/unexpected/超时/异常）
  - decompose：_decompose 关键词路由（plan/寒暄/其余 build，恒返回单个子任务）
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
from app.agent.base import AgentMessage
from app.agent.bus import AgentBus
from app.agent.supermod.decompose import SupervisorAgent
from app.config import settings


class FakeBus:
    def __init__(self, agents=("build", "explore", "plan"), reply=None):
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


async def _collect_one(agen):
    """取异步生成器的第一条（_route_to 单条回复场景）。"""
    out = [r async for r in agen]
    assert len(out) == 1
    return out[0]


# ── base ───────────────────────────────────────────────────────────────────

def test_timeout_for(agent, monkeypatch):
    monkeypatch.setattr(settings, "sub_agent_timeout", 150.0)
    monkeypatch.setattr(settings, "sub_agent_timeout_extended", 300.0)
    # build 是默认主 Agent（工具密集型）→ 长超时
    assert agent._timeout_for("build") == 300.0
    assert agent._timeout_for("plan") == 150.0
    assert agent._timeout_for("unknown") == 150.0


@pytest.mark.asyncio
async def test_heartbeat_touches_and_cancels(agent):
    beat = agent._start_heartbeat(interval=0.01)
    await asyncio.sleep(0.05)
    assert agent._bus.touched  # 心跳已 touch
    beat.cancel()
    await asyncio.sleep(0.01)


# ── decompose ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decompose_keyword_single(agent):
    # kb/code/web 关键词都合并到 build（单一默认主 Agent）
    assert await agent._decompose("帮我找文档里的情节") == [{"agent": "build", "question": "帮我找文档里的情节"}]
    assert await agent._decompose("写一个 python 函数") == [{"agent": "build", "question": "写一个 python 函数"}]
    assert await agent._decompose("查一下今天新闻") == [{"agent": "build", "question": "查一下今天新闻"}]


@pytest.mark.asyncio
async def test_decompose_explore_intent_goes_build(agent):
    """顶层只有 build/plan：探索意图不再路由 explore（explore 由 build 委派），统一走 build。"""
    assert await agent._decompose("帮我看看这个项目的目录结构") == \
        [{"agent": "build", "question": "帮我看看这个项目的目录结构"}]
    assert await agent._decompose("后端代码库有哪些文件") == \
        [{"agent": "build", "question": "后端代码库有哪些文件"}]


@pytest.mark.asyncio
async def test_decompose_plan_intent(agent):
    """规划/出方案意图走 plan（真实数据回归：'设计一个实施方案'曾漏配关键词→错投 build）。"""
    for q in (
        "请为『给 /api/monitor/stats 增加实时推送能力』设计一个实施方案",
        "给上传功能做一个方案",
        "先出一个方案，后续再讨论",
        "请制定项目的实施计划",
    ):
        assert await agent._decompose(q) == [{"agent": "plan", "question": q}], q


@pytest.mark.asyncio
async def test_decompose_no_llm_split_anymore(agent):
    """build 已合并全部能力，默认不再逐请求 LLM 拆子 Agent。"""
    assert await agent._decompose("帮我写代码并搜索新闻") == \
        [{"agent": "build", "question": "帮我写代码并搜索新闻"}]


@pytest.mark.asyncio
async def test_decompose_greeting_short(agent):
    assert await agent._decompose("你好") == [{"agent": "build", "question": "你好"}]
    assert agent._is_greeting("你好呀") is True


@pytest.mark.asyncio
async def test_decompose_short_non_greeting_goes_build(agent):
    assert await agent._decompose("写个爬虫") == [{"agent": "build", "question": "写个爬虫"}]


@pytest.mark.asyncio
async def test_decompose_returns_single_subtask_always(agent):
    """[C4] 恒返回单个子任务 —— 并行分支不可达的根因，改动此处必须同步回归。"""
    for q in ("帮我查资料然后顺便改改代码", "先规划再执行这个重构", "随便聊聊", "run the tests"):
        out = await agent._decompose(q)
        assert len(out) == 1, f"_decompose({q!r}) -> {out}"
        assert out[0]["question"] == q


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
        return [{"agent": "build", "question": q}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)
    replies = await _collect(agent, _msg(payload={"question": "q"}))
    assert replies[0].payload["routed_to"] == "build"


@pytest.mark.asyncio
async def test_handle_multi_subtasks_uses_first(agent, monkeypatch):
    """[C4] supervisor 不再 fan-out：即便 `_decompose` 返回多个，也只路由第一个
    （真机恒返回 1 个；此处是防御性契约，确保并行分支不会复活）。"""
    seen = []

    async def fake_route(target, payload, tid):
        seen.append(target)
        yield AgentMessage(source="supervisor", target="user", type="response", action="chat",
                           payload={"answer": "A", "routed_to": target}, thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "build", "question": "a"}, {"agent": "plan", "question": "b"}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)
    replies = await _collect(agent, _msg(payload={"question": "q"}))
    assert seen == ["build"]
    assert replies[0].payload["routed_to"] == "build"


@pytest.mark.asyncio
async def test_handle_filters_non_routable(agent, monkeypatch):
    """LLM 返回 supervisor → 白名单过滤 → 回退 build。"""
    async def fake_route(target, payload, tid):
        assert target == "build"
        yield AgentMessage(source="supervisor", target="user", type="response", action="chat",
                           payload={"answer": "R", "routed_to": target}, thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "supervisor", "question": "x"}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)
    replies = await _collect(agent, _msg(payload={"question": "q"}))
    assert replies[0].payload["routed_to"] == "build"


# ── _route_to ──────────────────────────────────────────────────────────────

def _reply(type="response", payload=None):
    return AgentMessage(source="sub", target="supervisor", type=type, action="chat",
                        payload=payload or {}, thread_id="sub")


@pytest.mark.asyncio
async def test_route_to_response(agent):
    agent._bus.reply = _reply(payload={"answer": "A", "tokens": {"input": 3}})
    agent._usage = {"input": 0, "output": 0}
    replies = [r async for r in agent._route_to("build", {"question": "q"}, "t1")]
    assert replies[0].payload["routed_to"] == "build"
    assert replies[0].payload["answer"] == "A"
    assert agent._usage["input"] == 3


@pytest.mark.asyncio
async def test_route_to_error(agent):
    agent._bus.reply = _reply(type="error", payload={"error": "boom", "error_type": "sub_agent_error", "completed_steps": []})
    replies = [r async for r in agent._route_to("build", {}, "t1")]
    assert replies[0].type == "error"
    assert "boom" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_route_to_unexpected_type(agent):
    agent._bus.reply = _reply(type="weird")
    replies = [r async for r in agent._route_to("build", {}, "t1")]
    assert "unexpected type" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_route_to_timeout(agent):
    async def boom(msg, timeout=None):
        raise asyncio.TimeoutError()
    agent._bus.send_and_wait = boom
    replies = [r async for r in agent._route_to("build", {}, "t1")]
    assert replies[0].type == "error"
    assert replies[0].payload["error_type"] == "sub_agent_timeout"
    assert "已完成步骤: 步骤1" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_route_to_exception(agent):
    async def boom(msg, timeout=None):
        raise RuntimeError("bus dead")
    agent._bus.send_and_wait = boom
    replies = [r async for r in agent._route_to("build", {}, "t1")]
    assert replies[0].payload["error_type"] == "sub_agent_error"


# ── plan→build 顺序交接（opencode build-switch 语义）──────────────────────────

def test_should_handoff_to_build(agent):
    assert agent._should_handoff_to_build("先做一个实施方案，然后执行") is True
    assert agent._should_handoff_to_build("请先规划再实现登录功能") is True
    assert agent._should_handoff_to_build("make a plan and then execute it") is True
    assert agent._should_handoff_to_build("请给我一个设计方案") is False
    assert agent._should_handoff_to_build("你好") is False


def _plan_like(target, answer="## 实施计划\n### 步骤1: 创建文件", plan_path="/x/plan.md"):
    return AgentMessage(source="supervisor", target="user", type="response", action="chat",
                        payload={"answer": answer, "plan_path": plan_path,
                                 "routed_to": target, "sources": [], "steps": [],
                                 "tokens": {"input": 1, "output": 1}},
                        thread_id="t1")


@pytest.mark.asyncio
async def test_handle_plan_then_build_handoff(agent, monkeypatch):
    """规划+执行意图 → plan 产出计划后自动交给 build 执行，合并为单条回复。"""
    calls = []

    async def fake_route(target, payload, tid):
        calls.append((target, payload["question"]))
        if target == "plan":
            yield _plan_like("plan")
        else:
            yield AgentMessage(source="supervisor", target="user", type="response", action="chat",
                               payload={"answer": "已执行步骤1", "routed_to": "build",
                                        "sources": [], "steps": [], "tokens": {"input": 2, "output": 2}},
                               thread_id=tid)

    async def fake_decompose(q):
        return [{"agent": "plan", "question": q}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)

    replies = await _collect(agent, _msg(payload={"question": "先做一个实施方案，然后执行"}))
    assert len(replies) == 1
    r = replies[0]
    assert r.type == "response"
    assert r.payload["routed_to"] == "plan→build"
    assert r.payload["plan_path"] == "/x/plan.md"
    assert "## 实施计划" in r.payload["answer"]
    assert "## 执行结果" in r.payload["answer"]
    assert "已执行步骤1" in r.payload["answer"]
    # 顺序：先 plan 后 build；build 收到的是计划执行指令（含计划文本）
    assert [t for t, _ in calls] == ["plan", "build"]
    assert "步骤1" in calls[1][1]


@pytest.mark.asyncio
async def test_handle_plan_no_handoff_without_execution_intent(agent, monkeypatch):
    """只请求规划（无执行意图）→ 仅返回计划，不触发 build。"""
    async def fake_route(target, payload, tid):
        assert target == "plan"
        yield _plan_like("plan")
    async def fake_decompose(q):
        return [{"agent": "plan", "question": q}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)

    replies = await _collect(agent, _msg(payload={"question": "请给我一个设计方案"}))
    assert len(replies) == 1
    assert replies[0].type == "response"
    assert replies[0].payload["routed_to"] == "plan"


@pytest.mark.asyncio
async def test_handle_plan_build_handoff_build_error_keeps_plan(agent, monkeypatch):
    """build 执行出错时：仍返回计划文本，并透传 build 错误信息。"""
    async def fake_route(target, payload, tid):
        if target == "plan":
            yield _plan_like("plan")
        else:
            yield AgentMessage(source="supervisor", target="user", type="error", action="chat",
                               payload={"error": "执行失败", "error_type": "sub_agent_error",
                                        "completed_steps": ["s1"]},
                               thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "plan", "question": q}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)

    replies = await _collect(agent, _msg(payload={"question": "先做计划，然后执行"}))
    assert len(replies) == 1
    r = replies[0]
    assert r.type == "error"
    assert r.payload["error"] == "执行失败"
    assert r.payload["plan_path"] == "/x/plan.md"
    assert "## 实施计划" in r.payload["answer"]
    assert "执行结果（出错）" in r.payload["answer"]


@pytest.mark.asyncio
async def test_handle_plan_error_propagates(agent, monkeypatch):
    """plan 自身出错时不触发 build，直接透传 error。"""
    async def fake_route(target, payload, tid):
        yield AgentMessage(source="supervisor", target="user", type="error", action="chat",
                           payload={"error": "plan 挂了", "error_type": "sub_agent_error",
                                    "completed_steps": []},
                           thread_id=tid)
    async def fake_decompose(q):
        return [{"agent": "plan", "question": q}]
    monkeypatch.setattr(agent, "_decompose", fake_decompose)
    monkeypatch.setattr(agent, "_route_to", fake_route)

    replies = await _collect(agent, _msg(payload={"question": "先规划，再执行"}))
    assert len(replies) == 1
    assert replies[0].type == "error"
    assert "plan 挂了" in replies[0].payload["error"]


# ── [C2] _route_to 必须发「本轮增量」而非累计值 ─────────────────────────────

class _TokenBus:
    """按目标 Agent 返回不同 usage 的假 bus（走真实 _route_to）。"""

    def __init__(self, by_target):
        self._by_target = by_target
        self.calls = []

    async def send_and_wait(self, msg, timeout=None):
        self.calls.append(msg.target)
        return self._by_target[msg.target]


def _usage_reply(answer, tokens, cost=0.0, routed_to="x", plan_path=""):
    return AgentMessage(source="sub", target="supervisor", type="response", action="chat",
                        payload={"answer": answer, "tokens": tokens, "cost": cost,
                                 "routed_to": routed_to, "sources": [], "steps": [],
                                 "plan_path": plan_path},
                        thread_id="sub-thread")


@pytest.mark.asyncio
async def test_route_to_emits_per_route_usage_delta(monkeypatch):
    """两条回复的 tokens 必须能相加得到总量，而不是 P 与 P+B（计划被计两次）。"""
    plan_usage = {"input": 100, "output": 10}
    build_usage = {"input": 500, "output": 50}
    bus = _TokenBus({
        "plan": _usage_reply("计划", plan_usage, cost=0.1, routed_to="plan", plan_path="/p.md"),
        "build": _usage_reply("执行", build_usage, cost=0.9, routed_to="build"),
    })
    sup = SupervisorAgent(bus)
    sup._usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    sup._cost = 0.0

    plan_reply = await _collect_one(sup._route_to("plan", {"question": "q"}, "t1"))
    build_reply = await _collect_one(sup._route_to("build", {"question": "q"}, "t1"))

    assert plan_reply.payload["tokens"]["input"] == 100, "第一条应是 plan 自身的用量"
    assert build_reply.payload["tokens"]["input"] == 500, "第二条应是 build 自身的用量"
    assert plan_reply.payload["cost"] == 0.1
    assert build_reply.payload["cost"] == 0.9

    merged = SupervisorAgent._merge_plan_build_reply(plan_reply, build_reply)
    assert merged.payload["tokens"]["input"] == 600, "合并后应是 100+500，不能是 100+600"
    assert merged.payload["tokens"]["output"] == 60
    assert merged.payload["cost"] == 1.0


@pytest.mark.asyncio
async def test_route_to_single_route_delta_matches_subagent(monkeypatch):
    """单路由时增量必须恰好等于子 Agent 上报的用量（回归保护）。"""
    bus = _TokenBus({"rag": _usage_reply("答案", {"input": 7, "output": 3}, cost=0.25)})
    sup = SupervisorAgent(bus)
    sup._usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    sup._cost = 0.0
    reply = await _collect_one(sup._route_to("rag", {"question": "q"}, "t1"))
    assert reply.payload["tokens"]["input"] == 7
    assert reply.payload["tokens"]["output"] == 3
    assert reply.payload["cost"] == 0.25