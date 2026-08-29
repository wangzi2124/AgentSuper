# -*- coding: utf-8 -*-
"""SupervisorAgent 拆分锁定用例（A3 补全：supervisor.py → supermod/{constants,base,core,decompose,parallel}）。

验证 OOTB 契约：
  - facade 仍导出 SupervisorAgent / DECOMPOSE_SYSTEM_PROMPT / SYNTHESIS_SYSTEM_PROMPT / SUB_RESULT_TRUNC / logger
  - 继承切片 MRO：SupervisorAgent -> SupervisorAgentDecompose -> SupervisorAgentCore -> SupervisorAgentBase -> BaseAgent
  - 跨块方法/类属性经 MRO 正确解析（_route_to/_decompose/_synthesize/_execute_parallel 等）
  - 行为不变：_is_greeting / _validate_subtasks 白名单+上限 / _timeout_for 分级超时 /
    _decompose 关键词快速路径 / _llm_decompose 失败回退 rag（两次尝试）
运行：pytest tests/test_supervisor_agent.py
"""
import asyncio
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import litellm

import app.agent.supervisor as sv
from app.agent.base import AgentMessage
from app.agent.supervisor import SupervisorAgent
from app.config import settings


class _StubBus:
    """只实现 supervisor 依赖的三个方法。"""

    def __init__(self, agents=None, send=None):
        self._agents = agents or ["rag", "web_search", "code"]
        self._send = send
        self.touched = []

    def list_agents(self):
        return list(self._agents)

    def touch(self, agent_id):
        self.touched.append(agent_id)

    async def send_and_wait(self, msg, timeout=None):
        if self._send is None:
            raise AssertionError("send_and_wait not stubbed")
        return await self._send(msg, timeout)


def _resp(answer="A", sources=None, tokens=None, is_error=False, error=""):
    if is_error:
        return AgentMessage(type="error", action="chat", payload={"error": error},
                            source="sub", target="supervisor")
    return AgentMessage(type="response", action="chat",
                        payload={"answer": answer, "sources": sources or [],
                                 "tokens": tokens or {}},
                        source="sub", target="supervisor")


def test_facade_exports_intact():
    for name in ("SupervisorAgent", "DECOMPOSE_SYSTEM_PROMPT",
                 "SYNTHESIS_SYSTEM_PROMPT", "SUB_RESULT_TRUNC", "logger"):
        assert hasattr(sv, name), name
    assert sv.SUB_RESULT_TRUNC > 0
    assert "rag" in sv.DECOMPOSE_SYSTEM_PROMPT


def test_mro_chain_and_method_placement():
    mro = [c.__name__ for c in SupervisorAgent.__mro__]
    assert mro.index("SupervisorAgent") < mro.index("SupervisorAgentDecompose") < \
        mro.index("SupervisorAgentCore") < mro.index("SupervisorAgentBase") < \
        mro.index("BaseAgent")
    for m in ("handle_message", "_route_to", "_decompose", "_is_greeting",
              "_llm_decompose", "_validate_subtasks", "_execute_parallel", "_synthesize"):
        assert callable(getattr(SupervisorAgent, m)), m
    # 类属性经 MRO 可达
    assert SupervisorAgent.ROUTABLE_AGENTS == {"rag", "web_search", "code"}
    assert len(SupervisorAgent._GREETING_KEYWORDS)  # 非空


def test_validate_subtasks_whitelist_and_cap():
    routable = ["rag", "web_search"]
    data = [
        {"agent": "rag", "question": "  Q1  "},
        {"agent": "supervisor", "question": "self"},
        {"agent": "code", "question": "not-routable"},
        {"agent": "web_search", "question": "Q2"},
        {"agent": "rag", "question": "Q3"},
        {"agent": "rag", "question": "Q4"},
        None,
        {"question": "no-agent"},
    ]
    got = SupervisorAgent._validate_subtasks(data, routable)
    assert got == [{"agent": "rag", "question": "Q1"},
                   {"agent": "web_search", "question": "Q2"},
                   {"agent": "rag", "question": "Q3"}]


def test_is_greeting_and_timeout_for():
    ag = SupervisorAgent(_StubBus())
    assert ag._is_greeting("你好") is True
    assert ag._is_greeting("麻烦你帮我查资料") is True
    assert ag._is_greeting("今天天气如何") is False
    assert ag._timeout_for("rag") == settings.sub_agent_timeout
    assert ag._timeout_for("unknown") == settings.sub_agent_timeout


def test_decompose_keyword_fast_path():
    async def main():
        ag = SupervisorAgent(_StubBus())
        code_only = await ag._decompose("帮我写个 Python 爬虫代码")
        assert code_only == [{"agent": "code", "question": "帮我写个 Python 爬虫代码"}]
        web_only = await ag._decompose("查一下今天的最新新闻")
        assert web_only == [{"agent": "web_search", "question": "查一下今天的最新新闻"}]
        greet_short = await ag._decompose("你好呀")
        assert greet_short == [{"agent": "rag", "question": "你好呀"}]

    asyncio.run(main())


def test_llm_decompose_fallback_to_rag(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("llm unreachable")

    monkeypatch.setattr(litellm, "acompletion", boom)

    async def main():
        ag = SupervisorAgent(_StubBus())
        got = await ag._llm_decompose("一个足够复杂到必然走 LLM 的问题", ["rag", "web_search", "code"])
        assert got == [{"agent": "rag", "question": "一个足够复杂到必然走 LLM 的问题"}]

    asyncio.run(main())


def test_execute_parallel_one_success_plus_error():
    async def send(msg, timeout):
        if msg.target == "code":
            return _resp(is_error=True, error="插件不可用")
        return _resp(answer="知识库答案", sources=[{"t": "s1"}])

    async def main():
        ag = SupervisorAgent(_StubBus(send=send))
        out = await ag._execute_parallel(
            [{"agent": "rag", "question": "Q1"}, {"agent": "code", "question": "Q2"}],
            {"question": "origin"}, "thr1",
        )
        assert out.type == "response"
        assert out.payload["answer"] == "知识库答案"
        assert out.payload["routed_to"] == "rag"

    asyncio.run(main())


def test_handle_message_direct_route():
    async def send(msg, timeout):
        assert msg.target == "rag"
        assert msg.type == "request"
        return _resp(answer="回的")

    async def main():
        ag = SupervisorAgent(_StubBus(send=send))
        msg = AgentMessage(type="request", action="chat", payload={"question": "你好"},
                           source="user", target="supervisor", thread_id="t0")
        replies = [r async for r in ag.handle_message(msg)]
        assert len(replies) == 1
        assert replies[0].type == "response"
        assert replies[0].payload["routed_to"] == "rag"
        assert ag._usage == {"input": 0, "output": 0}

    asyncio.run(main())


def test_handler_rejects_unknown_action():
    async def main():
        ag = SupervisorAgent(_StubBus())
        msg = AgentMessage(type="request", action="nonsense", payload={},
                           source="user", target="supervisor", thread_id="t0")
        replies = [r async for r in ag.handle_message(msg)]
        assert replies and replies[0].type == "error"
        assert replies[0].payload["error"].startswith("Supervisor doesn't support")

    asyncio.run(main())