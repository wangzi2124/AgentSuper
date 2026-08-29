# -*- coding: utf-8 -*-
"""多 Agent 并发处理单问题的快速回归用例（离线，无需 LLM/知识库）。

壁钟计时证明「并发」而非「串行」：真实 AgentBus + 事件循环 + 会真实 sleep
的假子 Agent。单例耗时 ~2.5s，阈值取 2×max_delay 为并行的上界（串行会是
3×max_delay，分离度足够，避免 CI 抖动误报）。

对应可执行脚本注意它的长延时版：scripts/test_multi_agent_parallel.py
"""
import asyncio
import time

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.agent.supervisor import SupervisorAgent


class _FakeSubAgent(BaseAgent):
    """可真实 sleep 的假子 Agent；outcome 控制 ok / error / raise / silent。"""

    def __init__(self, agent_id, delay, answer="OK", outcome="ok"):
        self.id = agent_id
        self.delay = delay
        self.answer = answer
        self.outcome = outcome
        self.started_at = None

    @property
    def agent_id(self) -> str:
        return self.id

    async def handle_message(self, msg: AgentMessage):
        self.started_at = time.perf_counter()
        await asyncio.sleep(self.delay)
        if self.outcome == "raise":
            raise RuntimeError(f"[{self.id}] agent crashed")
        if self.outcome == "error":
            yield AgentMessage(
                type="error", action=msg.action,
                payload={"error": f"[{self.id}] 业务失败", "error_type": "sub_agent_error",
                         "completed_steps": [f"{self.id}_step1"]},
                source=self.id, target=msg.source, thread_id=msg.thread_id,
            )
            return
        yield AgentMessage(
            type="response", action=msg.action,
            payload={"answer": self.answer, "sources": [{"title": f"{self.id} 来源"}],
                     "tokens": {"input": 5, "output": 7}},
            source=self.id, target=msg.source, thread_id=msg.thread_id,
        )


class _TestSupervisor(SupervisorAgent):
    """离线 supervisor：固定 `_decompose` 输出 + 无 LLM `_synthesize`，可收紧超时。"""

    def __init__(self, bus, subtasks=None, timeout_override=None):
        super().__init__(bus)
        self._forced_subtasks = subtasks
        self._timeout_override = timeout_override
        self.synthesize_called = False

    async def _decompose(self, question):
        if self._forced_subtasks is not None:
            return [dict(s) for s in self._forced_subtasks]
        return await super()._decompose(question)

    async def _synthesize(self, question, results):
        self.synthesize_called = True
        return "并行汇总：\n" + "\n".join(f"- [{r['agent']}] {r['answer']}" for r in results)

    def _timeout_for(self, agent_id):
        return self._timeout_override if self._timeout_override is not None else super()._timeout_for(agent_id)


async def _drive(spv: _TestSupervisor) -> list:
    msg = AgentMessage(type="request", action="chat",
                       payload={"question": "对比 A/B/C 三套实现方案，检索资料并产出文档"},
                       source="user", target="supervisor", thread_id="t-main")
    return [r async for r in spv.handle_message(msg)]


async def _run(subtasks, agents: dict, timeout_override=None):
    """起总线+子 Agent 循环 → 驱动 supervisor bootstrap 一个请求 → 清理。"""
    bus = AgentBus()
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    spv = _TestSupervisor(bus, subtasks=subtasks, timeout_override=timeout_override)
    try:
        start = time.perf_counter()
        replies = await _drive(spv)
        return spv, agents, replies, time.perf_counter() - start
    finally:
        bus.stop_all()
        await asyncio.sleep(0.02)


def _start_gap(agents: dict) -> float:
    starts = [a.started_at for a in agents.values() if a.started_at]
    return (max(starts) - min(starts)) if starts else 99.0


async def test_bus_level_concurrency():
    """3 个独立事件循环的 Agent 同时各睡 0.3s → 总耗时 ~0.3s（串行应 ≈0.9s）。"""
    agents = {
        "rag": _FakeSubAgent("rag", 0.3, "KB 答案"),
        "web_search": _FakeSubAgent("web_search", 0.3, "网络答案"),
        "code": _FakeSubAgent("code", 0.3, "代码答案"),
    }
    bus = AgentBus()
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    try:
        start = time.perf_counter()
        await asyncio.gather(*[
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": f"Q{i}"},
                             source="user", target=aid, thread_id=f"bus-{i}"),
                timeout=5,
            ) for i, aid in enumerate(agents)
        ])
        elapsed = time.perf_counter() - start
    finally:
        bus.stop_all()
        await asyncio.sleep(0.02)

    assert 0.25 < elapsed < 0.6, f"总耗时 {elapsed:.2f}s（并行应≈0.3s，串行应≈0.9s）"
    assert _start_gap(agents) < 0.3, "三个 Agent 未同时开始执行"


async def test_supervisor_parallel_fanout():
    """supervisor 分解→_execute_parallel：3 子任务并行且起始时刻互相重叠。"""
    agents = {
        "rag": _FakeSubAgent("rag", 0.3, "KB 答案"),
        "web_search": _FakeSubAgent("web_search", 0.3, "网络答案"),
        "code": _FakeSubAgent("code", 0.3, "代码答案"),
    }
    spv, _, replies, elapsed = await _run(
        [{"agent": "rag", "question": "Q1"}, {"agent": "web_search", "question": "Q2"},
         {"agent": "code", "question": "Q3"}], agents)

    reply = replies[0]
    assert reply.type == "response"
    assert reply.payload["routed_to"] == "rag+web_search+code"
    assert spv.synthesize_called
    assert "并行汇总" in reply.payload["answer"]
    assert 0.25 < elapsed < 0.6, f"总耗时 {elapsed:.2f}s（并行应≈0.3s，串行应≈0.9s）"
    assert _start_gap(agents) < 0.3


async def test_error_isolation():
    """rag 成功、web_search 业务失败、code 崩溃 → 其余不受影响仍产出答案。"""
    agents = {
        "rag": _FakeSubAgent("rag", 0.2, "KB 答案"),
        "web_search": _FakeSubAgent("web_search", 0.2, "", outcome="error"),
        "code": _FakeSubAgent("code", 0.2, "", outcome="raise"),
    }
    spv, _, replies, elapsed = await _run(
        [{"agent": "rag", "question": "Q1"}, {"agent": "web_search", "question": "Q2"},
         {"agent": "code", "question": "Q3"}], agents)

    reply = replies[0]
    assert reply.type == "response"
    assert reply.payload["routed_to"] == "rag"
    assert reply.payload["answer"] == "KB 答案"
    assert not spv.synthesize_called
    assert elapsed < 0.6


async def test_partial_failure_note():
    """2 成功 + 1 失败 → 多结果汇总且带 ⚠️ 部分 Agent 执行出错 说明。"""
    agents = {
        "rag": _FakeSubAgent("rag", 0.2, "KB 答案"),
        "web_search": _FakeSubAgent("web_search", 0.2, "网络答案"),
        "code": _FakeSubAgent("code", 0.2, "", outcome="error"),
    }
    spv, _, replies, _ = await _run(
        [{"agent": "rag", "question": "Q1"}, {"agent": "web_search", "question": "Q2"},
         {"agent": "code", "question": "Q3"}], agents)

    reply = replies[0]
    assert reply.payload["routed_to"] == "rag+web_search"
    assert spv.synthesize_called
    assert "⚠️ 部分 Agent 执行出错" in reply.payload["answer"]
    assert "web_search" in reply.payload["answer"] and "code" in reply.payload["answer"]


async def test_graded_timeout_no_hang():
    """不回复的 silent Agent：0.4s 超时 + 一次 0.4s 宽限 → 错误交付，不悬挂整个请求。"""
    agents = {
        "rag": _FakeSubAgent("rag", 0.2, "KB 答案"),
        "web_search": _FakeSubAgent("web_search", 0.2, "网络答案"),
        "code": _FakeSubAgent("code", 30, "", outcome="silent"),
    }
    _, _, replies, elapsed = await _run(
        [{"agent": "rag", "question": "Q1"}, {"agent": "web_search", "question": "Q2"},
         {"agent": "code", "question": "Q3"}], agents, timeout_override=0.4)

    reply = replies[0]
    assert reply.type == "response"
    assert "did not respond in time" in reply.payload["answer"]
    assert "⚠️" in reply.payload["answer"] and "code" in reply.payload["answer"]
    assert elapsed < 2.2, f"超时路径总耗时 {elapsed:.2f}s（应≈1.0s：0.4s 超时 + 0.4s 宽限）"


async def test_single_subtask_direct_route():
    """只拆出 1 个子任务 → 直接路由该 Agent，不调用 _synthesize。"""
    agents = {"rag": _FakeSubAgent("rag", 0.2, "单一答案")}
    spv, _, replies, _ = await _run(
        [{"agent": "rag", "question": "只拆出一个"}], agents)

    reply = replies[0]
    assert reply.type == "response"
    assert reply.payload["routed_to"] == "rag"
    assert reply.payload["answer"] == "单一答案"
    assert not spv.synthesize_called