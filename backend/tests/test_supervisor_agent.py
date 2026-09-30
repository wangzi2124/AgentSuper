# -*- coding: utf-8 -*-
"""SupervisorAgent 拆分锁定用例（supervisor.py → supermod/{constants,base,core,decompose}）。

[C4] `parallel` 切片已删除（`_execute_parallel` / `_synthesize` 恒不可达），
    继承链由 4 级收敛为 3 级；末级 `SupervisorAgent` 迁到 `supermod/decompose.py`。
    `DECOMPOSE_SYSTEM_PROMPT` / `SYNTHESIS_SYSTEM_PROMPT` / `SUB_RESULT_TRUNC`
    与 `_llm_decompose` / `_validate_subtasks` 随并行分解一并删除。

验证 OOTB 契约：
  - facade 仍导出 SupervisorAgent / logger（并行相关的三个常量已随 C4 移除）
  - 继承切片 MRO：SupervisorAgent -> SupervisorAgentDecompose -> SupervisorAgentCore -> SupervisorAgentBase -> BaseAgent
  - 跨块方法/类属性经 MRO 正确解析（_route_to/_decompose/_is_greeting）
  - 行为不变：_is_greeting / _timeout_for 分级超时 / _decompose 关键词快速路径（恒单子任务）
运行：pytest tests/test_supervisor_agent.py
"""
import asyncio
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import app.agent.supervisor as sv
from app.agent.base import AgentMessage
from app.agent.supervisor import SupervisorAgent
from app.config import settings


class _StubBus:
    """只实现 supervisor 依赖的三个方法。"""

    def __init__(self, agents=None, send=None):
        self._agents = agents or ["build", "explore", "plan"]
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
    for name in ("SupervisorAgent", "logger"):
        assert hasattr(sv, name), name
    # [C4] 并行分解相关的三个常量已删除
    for gone in ("DECOMPOSE_SYSTEM_PROMPT", "SYNTHESIS_SYSTEM_PROMPT", "SUB_RESULT_TRUNC"):
        assert not hasattr(sv, gone), gone


def test_mro_chain_and_method_placement():
    mro = [c.__name__ for c in SupervisorAgent.__mro__]
    assert mro.index("SupervisorAgent") < mro.index("SupervisorAgentDecompose") < \
        mro.index("SupervisorAgentCore") < mro.index("SupervisorAgentBase") < \
        mro.index("BaseAgent")
    for m in ("handle_message", "_route_to", "_decompose", "_is_greeting"):
        assert callable(getattr(SupervisorAgent, m)), m
    # [C4] 并行/分解实现已移除
    for gone in ("_llm_decompose", "_validate_subtasks",
                 "_execute_parallel", "_synthesize"):
        assert not hasattr(SupervisorAgent, gone), gone
    # 类属性经 MRO 可达（rag/code/web_search 已合并为 build）
    assert SupervisorAgent.ROUTABLE_AGENTS == {"build", "plan"}
    assert len(SupervisorAgent._GREETING_KEYWORDS)  # 非空


def test_is_greeting_and_timeout_for():
    ag = SupervisorAgent(_StubBus())
    assert ag._is_greeting("你好") is True
    assert ag._is_greeting("麻烦你帮我查资料") is True
    assert ag._is_greeting("今天天气如何") is False
    assert ag._timeout_for("build") == settings.sub_agent_timeout_extended
    assert ag._timeout_for("unknown") == settings.sub_agent_timeout


def test_decompose_keyword_fast_path():
    async def main():
        ag = SupervisorAgent(_StubBus())
        code_only = await ag._decompose("帮我写个 Python 爬虫代码")
        assert code_only == [{"agent": "build", "question": "帮我写个 Python 爬虫代码"}]
        web_only = await ag._decompose("查一下今天的最新新闻")
        assert web_only == [{"agent": "build", "question": "查一下今天的最新新闻"}]
        greet_short = await ag._decompose("你好呀")
        assert greet_short == [{"agent": "build", "question": "你好呀"}]
        plan_intent = await ag._decompose("请先出一个实施方案")
        assert plan_intent == [{"agent": "plan", "question": "请先出一个实施方案"}]

    asyncio.run(main())


def test_decompose_never_fans_out():
    """[C4] supervisor 不做并行分解：恒返回单个子任务。"""
    async def main():
        ag = SupervisorAgent(_StubBus())
        for q in ("查资料顺便改代码", "先规划再执行", "写个函数", "跑一下测试"):
            out = await ag._decompose(q)
            assert len(out) == 1, f"_decompose({q!r}) -> {out}"

    asyncio.run(main())


def test_handle_message_direct_route():
    async def send(msg, timeout):
        assert msg.target == "build"
        assert msg.type == "request"
        return _resp(answer="回的")

    async def main():
        ag = SupervisorAgent(_StubBus(send=send))
        msg = AgentMessage(type="request", action="chat", payload={"question": "你好"},
                           source="user", target="supervisor", thread_id="t0")
        replies = [r async for r in ag.handle_message(msg)]
        assert len(replies) == 1
        assert replies[0].type == "response"
        assert replies[0].payload["routed_to"] == "build"
        assert ag._usage == {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
        assert ag._cost == 0.0

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