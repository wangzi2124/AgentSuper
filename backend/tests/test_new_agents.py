# -*- coding: utf-8 -*-
"""ExploreAgent / PlanAgent 全量用例（mock LLM，无真实网络）。

覆盖：
  - ExploreAgent：chat 动作、非 request 消息忽略、异常兜底、记忆读写
  - PlanAgent：chat 动作、非 request 消息忽略、异常兜底、记忆读写、LLM 调用
  - stream_events：explore/plan 的 label/avatar
  - supermod/constants：DECOMPOSE_SYSTEM_PROMPT 包含新 agent
  - runtime：注册 explore/plan agent

运行：pytest tests/test_new_agents.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent.base import AgentMessage
from app.agent.explore_agent import ExploreAgent
from app.agent.plan_agent import PlanAgent
from app.agent.memory import MemoryManager
from app.agent.stream_events import agent_meta, AGENT_LABELS, AGENT_AVATARS


def _msg(agent_id, action="chat", payload=None, type="request", thread="t1"):
    return AgentMessage(source="supervisor", target=agent_id, type=type,
                        action=action, payload=payload or {}, thread_id=thread)


async def _collect(agent, msg):
    return [r async for r in agent.handle_message(msg)]


class FakeMemory(MemoryManager):
    def __init__(self, tmp_path):
        super().__init__(persist_path=str(tmp_path / "m.json"))
        self.sets = []
        self.gets = []

    async def get(self, key, default=None, namespace=""):
        self.gets.append((key, namespace))
        return await super().get(key, default, namespace)

    async def set(self, key, value, ttl=None, tags=None, namespace=""):
        self.sets.append((key, namespace, tags))
        return await super().set(key, value, ttl, tags, namespace)


# ── ExploreAgent ─────────────────────────────────────────────────────────

def test_explore_agent_id():
    agent = ExploreAgent()
    assert agent.agent_id == "explore"


def test_explore_agent_custom_id():
    agent = ExploreAgent(agent_id="my_explore")
    assert agent.agent_id == "my_explore"


@pytest.mark.asyncio
async def test_explore_non_request_returns_nothing():
    agent = ExploreAgent()
    replies = await _collect(agent, _msg("explore", type="response"))
    assert replies == []


@pytest.mark.asyncio
async def test_explore_chat_action(monkeypatch, tmp_path):
    mm = FakeMemory(tmp_path)
    agent = ExploreAgent(memory=mm)

    async def fake_tool_loop(**kwargs):
        return "探索结果：找到 3 个文件"
    monkeypatch.setattr("app.agent.explore_agent.tool_loop_chat", fake_tool_loop)

    q = asyncio.Queue()
    replies = await _collect(agent, _msg(
        "explore",
        payload={"question": "项目结构是什么？", "conversation_id": "cid", "_event_queue": q}
    ))
    assert len(replies) == 1
    assert replies[0].type == "response"
    assert replies[0].payload["answer"] == "探索结果：找到 3 个文件"
    # 记忆已写入
    assert any(k == "explore_last_q" for k, _, _ in mm.sets)


@pytest.mark.asyncio
async def test_explore_passes_allowlist_from_spec(monkeypatch):
    """工具 allowlist 由规格注册表驱动：explore 只把只读工具集传给 tool_loop_chat。"""
    from app.agent.agent_specs import get_agent_spec

    seen = {}

    async def fake_tool_loop(**kwargs):
        seen.update(kwargs)
        return "答案"
    monkeypatch.setattr("app.agent.explore_agent.tool_loop_chat", fake_tool_loop)
    agent = ExploreAgent()
    await _collect(agent, _msg("explore", payload={"question": "q"}))
    assert "allowlist" in seen
    assert tuple(seen["allowlist"]) == get_agent_spec("explore").tools
    assert "readonly" not in seen  # 散落的布尔已移除


@pytest.mark.asyncio
async def test_explore_chat_emits_events(monkeypatch, tmp_path):
    agent = ExploreAgent()

    async def fake_tool_loop(**kwargs):
        return "答案"
    monkeypatch.setattr("app.agent.explore_agent.tool_loop_chat", fake_tool_loop)

    q = asyncio.Queue()
    await _collect(agent, _msg(
        "explore",
        payload={"question": "q", "_event_queue": q}
    ))

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e["type"] for e in events]
    assert "agent_start" in types
    assert "agent_step" in types
    assert "agent_done" in types
    # agent_start 应包含 explore 的 meta
    start = next(e for e in events if e["type"] == "agent_start")
    assert start["agent_id"] == "explore"
    assert start["agent_name"] == "代码探索"
    assert start["agent_avatar"] == "🔍"


@pytest.mark.asyncio
async def test_explore_unknown_action():
    agent = ExploreAgent()
    replies = await _collect(agent, _msg("explore", action="unknown"))
    assert len(replies) == 1
    assert replies[0].type == "error"
    assert "Unknown action" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_explore_exception_emits_error(monkeypatch):
    agent = ExploreAgent()

    async def boom(**kwargs):
        raise RuntimeError("LLM exploded")
    monkeypatch.setattr("app.agent.explore_agent.tool_loop_chat", boom)

    q = asyncio.Queue()
    replies = await _collect(agent, _msg(
        "explore",
        payload={"question": "q", "_event_queue": q}
    ))
    assert len(replies) == 1
    assert replies[0].type == "error"
    assert "LLM exploded" in replies[0].payload["error"]
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["type"] == "agent_error" for e in events)


# ── PlanAgent ────────────────────────────────────────────────────────────

def test_plan_agent_id():
    agent = PlanAgent()
    assert agent.agent_id == "plan"


def test_plan_agent_custom_id():
    agent = PlanAgent(agent_id="my_plan")
    assert agent.agent_id == "my_plan"


@pytest.mark.asyncio
async def test_plan_non_request_returns_nothing():
    agent = PlanAgent()
    replies = await _collect(agent, _msg("plan", type="response"))
    assert replies == []


@pytest.mark.asyncio
async def test_plan_chat_action(monkeypatch, tmp_path):
    mm = FakeMemory(tmp_path)
    agent = PlanAgent(memory=mm)

    async def fake_gen_plan(question, history, **kwargs):
        return "## 实施计划\n### 步骤 1: 创建文件\n..."
    monkeypatch.setattr(agent, "_generate_plan", fake_gen_plan)

    q = asyncio.Queue()
    replies = await _collect(agent, _msg(
        "plan",
        payload={"question": "实现登录功能", "conversation_id": "cid", "_event_queue": q}
    ))
    assert len(replies) == 1
    assert replies[0].type == "response"
    assert "实施计划" in replies[0].payload["answer"]
    # 记忆已写入
    assert any(k == "plan_last_q" for k, _, _ in mm.sets)


@pytest.mark.asyncio
async def test_plan_chat_emits_events(monkeypatch):
    agent = PlanAgent()

    async def fake_gen_plan(question, history, **kwargs):
        return "计划"
    monkeypatch.setattr(agent, "_generate_plan", fake_gen_plan)

    q = asyncio.Queue()
    await _collect(agent, _msg(
        "plan",
        payload={"question": "q", "_event_queue": q}
    ))

    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e["type"] for e in events]
    assert "agent_start" in types
    assert "agent_step" in types
    assert "agent_done" in types
    start = next(e for e in events if e["type"] == "agent_start")
    assert start["agent_id"] == "plan"
    assert start["agent_name"] == "规划模式"
    assert start["agent_avatar"] == "📋"


@pytest.mark.asyncio
async def test_plan_generate_reasoning_content_fallback(monkeypatch):
    """deepseek-v4-flash 把计划写进 reasoning_content、content 为空时的回退（真实数据回归）。"""
    import app.agent.plan_agent as pa

    msg = SimpleNamespace(content="", reasoning_content="这是 reasoning 里的计划正文")
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )

    async def fake_acompletion(**kw):
        return resp

    monkeypatch.setattr(pa.litellm, "acompletion", fake_acompletion)
    agent = PlanAgent()
    out = await agent._generate_plan("设计一个实施方案", [])
    assert out == "这是 reasoning 里的计划正文"


@pytest.mark.asyncio
async def test_plan_generate_content_preferred(monkeypatch):
    """content 非空时优先取 content，不被 reasoning_content 覆盖。"""
    import app.agent.plan_agent as pa

    msg = SimpleNamespace(content="## 实施计划\n正式内容", reasoning_content="思考过程")
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )

    async def fake_acompletion(**kw):
        return resp

    monkeypatch.setattr(pa.litellm, "acompletion", fake_acompletion)
    agent = PlanAgent()
    out = await agent._generate_plan("q", [])
    assert out == "## 实施计划\n正式内容"


@pytest.mark.asyncio
async def test_plan_unknown_action():
    agent = PlanAgent()
    replies = await _collect(agent, _msg("plan", action="unknown"))
    assert len(replies) == 1
    assert replies[0].type == "error"
    assert "Unknown action" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_plan_exception_emits_error(monkeypatch):
    agent = PlanAgent()

    async def boom(question, history, **kwargs):
        raise RuntimeError("LLM timeout")
    monkeypatch.setattr(agent, "_generate_plan", boom)

    q = asyncio.Queue()
    replies = await _collect(agent, _msg(
        "plan",
        payload={"question": "q", "_event_queue": q}
    ))
    assert len(replies) == 1
    assert replies[0].type == "error"
    assert "LLM timeout" in replies[0].payload["error"]
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["type"] == "agent_error" for e in events)


@pytest.mark.asyncio
async def test_plan_agent_delegates_via_tool_loop(monkeypatch):
    """plan 有 bus 时走工具循环：把 bus + task_subagents(explore) + allowlist(tool_task) 传入。"""
    import app.agent.plan_agent as pa
    seen = {}

    async def fake_loop(**kw):
        seen.update(kw)
        return "## 实施计划\n### 步骤 1"
    monkeypatch.setattr(pa, "tool_loop_chat", fake_loop)

    bus = object()
    agent = PlanAgent(bus=bus)
    out = await agent._generate_plan("q", [], event_queue=None, conv_id="cid", directory="/wd")
    assert out.startswith("## 实施计划")
    assert seen["bus"] is bus
    assert seen["task_subagents"] == ("explore",)
    assert seen["allowlist"] == ("tool_task",)
    assert seen["conversation_id"] == "cid"
    assert seen["directory"] == "/wd"


@pytest.mark.asyncio
async def test_plan_agent_no_bus_uses_pure_llm(monkeypatch):
    """无 bus（单测/降级）→ 仍走纯 LLM 路径，不调用 tool_loop_chat。"""
    import app.agent.plan_agent as pa

    async def boom_loop(**kw):
        raise AssertionError("无 bus 不应走工具循环")
    monkeypatch.setattr(pa, "tool_loop_chat", boom_loop)

    async def fake_acompletion(**kw):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="纯 LLM 计划"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
    monkeypatch.setattr(pa.litellm, "acompletion", fake_acompletion)
    agent = PlanAgent()
    assert await agent._generate_plan("q", []) == "纯 LLM 计划"


# ── stream_events：新 agent 的 label/avatar ──────────────────────────────

def test_explore_meta():
    name, avatar = agent_meta("explore")
    assert name == "代码探索"
    assert avatar == "🔍"


def test_plan_meta():
    name, avatar = agent_meta("plan")
    assert name == "规划模式"
    assert avatar == "📋"


def test_new_agents_in_labels_and_avatars():
    assert "explore" in AGENT_LABELS
    assert "plan" in AGENT_LABELS
    assert "explore" in AGENT_AVATARS
    assert "plan" in AGENT_AVATARS


# ── supermod/constants：DECOMPOSE_SYSTEM_PROMPT 包含新 agent ───────────

def test_decompose_prompt_includes_new_agents():
    from app.agent.supermod.constants import DECOMPOSE_SYSTEM_PROMPT
    # 顶层只有 build/plan 两个命令；explore 是委派目标，不在路由提示词里
    assert "explore" not in DECOMPOSE_SYSTEM_PROMPT
    assert "plan" in DECOMPOSE_SYSTEM_PROMPT
    assert "规划" in DECOMPOSE_SYSTEM_PROMPT


def test_routable_agents_includes_new():
    from app.agent.supermod.base import SupervisorAgentBase
    # 对齐 opencode：顶层命令只有 build/plan，explore 由委派进入
    assert SupervisorAgentBase.ROUTABLE_AGENTS == {"build", "plan"}
    assert "explore" not in SupervisorAgentBase.ROUTABLE_AGENTS
    assert "plan" in SupervisorAgentBase.ROUTABLE_AGENTS


# ── graphmod/constants：_TASK_TOOL_SUBAGENTS 只含 explore/plan ─────────────

def test_task_tool_subagents_includes_new():
    from app.agent.graphmod.constants import _TASK_TOOL_SUBAGENTS, _TASK_TOOL_SCHEMA
    assert _TASK_TOOL_SUBAGENTS == ("explore", "plan")
    assert "explore" in _TASK_TOOL_SUBAGENTS
    assert "plan" in _TASK_TOOL_SUBAGENTS
    # schema enum 也应包含
    enum = _TASK_TOOL_SCHEMA["function"]["parameters"]["properties"]["subagent_type"]["enum"]
    assert "explore" in enum
    assert "plan" in enum
