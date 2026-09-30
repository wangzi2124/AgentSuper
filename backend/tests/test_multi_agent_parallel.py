# -*- coding: utf-8 -*-
"""AgentBus 层并发 + 分级超时的离线回归（无需 LLM/知识库）。

[C4] 原文件同时覆盖 supervisor 的并行 fan-out（`_execute_parallel` /
`_synthesize` / 多子任务错误隔离），这些实现已删除 —— fan-out 恒不可达。
保留并改写的是**仍然有效**的两类保障：
  1. 总线层并发：多个 Agent 各自真实 sleep，并发总耗时 ≈ 单个 delay
  2. 分级超时：silent Agent 超时 + 一次宽限 → 错误按期交付，不悬挂整个请求

对应可执行脚本注意它的长延时版：scripts/test_multi_agent_parallel.py
"""
import asyncio
import time

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus


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


def _start_gap(agents: dict) -> float:
    starts = [a.started_at for a in agents.values() if a.started_at]
    return (max(starts) - min(starts)) if starts else 99.0


async def test_bus_level_concurrency():
    """3 个 Agent 同时各睡 0.3s → 总耗时 ~0.3s（串行应 ≈0.9s）。"""
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
        results = await asyncio.gather(*[
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
    assert {r.payload["answer"] for r in results} == {"KB 答案", "网络答案", "代码答案"}


async def test_error_isolation_at_bus_level():
    """一个 Agent 业务失败 / 一个崩溃 → 其余不受影响，仍各自拿到答案。"""
    agents = {
        "build": _FakeSubAgent("build", 0.2, "KB 答案"),
        "explore": _FakeSubAgent("explore", 0.2, "", outcome="error"),
        "plan": _FakeSubAgent("plan", 0.2, "", outcome="raise"),
    }
    bus = AgentBus()
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    try:
        replies = await asyncio.gather(*[
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": f"Q{i}"},
                             source="user", target=aid, thread_id=f"bus-{i}"),
                timeout=5,
            ) for i, aid in enumerate(agents)
        ], return_exceptions=True)
    finally:
        bus.stop_all()
        await asyncio.sleep(0.02)

    by_target = {r.source: r for r in replies if not isinstance(r, BaseException)}
    assert by_target["build"].payload["answer"] == "KB 答案"
    # 崩溃与业务失败都归一为 error 消息交付（不裸抛、不污染其余 Agent）
    assert by_target["explore"].type == "error"
    assert "业务失败" in by_target["explore"].payload["error"]
    assert by_target["plan"].type == "error"
    assert "crashed" in by_target["plan"].payload["error"]


async def test_graded_timeout_no_hang():
    """silent Agent：0.4s 超时 + 一次 0.4s 宽限 → 错误按期交付，不悬挂整个请求。"""
    silent = _FakeSubAgent("silent", 30, "", outcome="silent")
    fast = _FakeSubAgent("fast", 0.2, "主答案")
    bus = AgentBus()
    bus.register(silent)
    bus.register(fast)
    bus.start_all()
    try:
        start = time.perf_counter()
        replies = await asyncio.gather(
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": "Q1"},
                             source="user", target="silent", thread_id="b1"),
                timeout=0.4,
            ),
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": "Q2"},
                             source="user", target="fast", thread_id="b2"),
                timeout=0.4,
            ),
            return_exceptions=True,
        )
        elapsed = time.perf_counter() - start
    finally:
        bus.stop_all()
        await asyncio.sleep(0.02)

    silent_reply, fast_reply = replies
    # [C9] 超时以异常交付（消息含真实最坏等待），调用方据此归一成中文错误
    assert isinstance(silent_reply, asyncio.TimeoutError)
    assert "No reply from 'silent' within 0.4s" in str(silent_reply)
    assert "max_with_grace=1s" in str(silent_reply)  # 0.4s + 一次 0.4s 宽限，而非翻倍到 0.8s 超时值
    assert not isinstance(fast_reply, BaseException)
    assert fast_reply.payload["answer"] == "主答案"
    assert elapsed < 2.0, f"超时路径总耗时 {elapsed:.2f}s（应≈0.8s：0.4s 超时 + 一次 ≤0.4s 宽限）"
