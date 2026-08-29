#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多 Agent 并发处理单问题的可执行测试脚本（离线，无需真实 LLM / 知识库）。

用法（Windows，直接用 backend/.venv 的解释器）：
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\test_multi_agent_parallel.py

原理：注册真实 AgentBus + 事件循环，子 Agent 在总线任务里真实 asyncio.sleep，
用墙钟时间证明「并发」而非「串行」：

  [1] bus 层并发        —— 3 路 gather 的 send_and_wait 总耗时 ≈ max(延迟)，
                          串行（单回调干等）会是 3×延迟；
  [2] supervisor 并行   —— _decompose→_execute_parallel(asyncio.gather)：
                          3 个子任务的总耗时 ≈ max(延迟)；
  [3] 真正交错执行      —— 3 个子 Agent 的 handle_message 起始时刻差 ≪ 单任务延迟
                          （证明它们在同一时刻同时 running）；
  [4] 错误隔离          —— 单个子 Agent 崩溃 / 业务失败不影响其余子 Agent 结果；
  [5] 局部失败提示      —— 多个成功 + 1 个失败时汇总带 ⚠️ 错误说明；
  [6] 分级超时+宽限     —— 不回复的子 Agent 走 _timeout_for 超时 → completed_steps
                          错误交付，不悬挂整个请求；
  [7] 单子任务简单路由  —— 只拆出 1 个子任务时不走 _synthesize。

退出码：全部通过 → 0；任一失败 → 1。
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.agent.base import AgentMessage
from app.agent.bus import AgentBus
from app.agent.supervisor import SupervisorAgent
from app.agent.base import BaseAgent


class FakeSubAgent(BaseAgent):
    """可在事件循环里真实 sleep 的假子 Agent；outcome 控制 ok / error / raise / silent。"""

    def __init__(self, agent_id: str, delay: float, answer: str = "OK", outcome: str = "ok"):
        self.id = agent_id
        self.delay = delay
        self.answer = answer
        self.outcome = outcome
        self.started_at = None
        self.finished_at = None

    @property
    def agent_id(self) -> str:
        return self.id

    async def handle_message(self, msg: AgentMessage):
        self.started_at = time.perf_counter()
        await asyncio.sleep(self.delay)
        self.finished_at = time.perf_counter()
        if self.outcome == "raise":
            raise RuntimeError(f"[{self.id}] agent crashed")
        payload = {
            "answer": self.answer,
            "sources": [{"title": f"{self.id} 来源"}],
            "tokens": {"input": 5, "output": 7},
        }
        if self.outcome == "error":
            yield AgentMessage(
                type="error", action=msg.action,
                payload={"error": f"[{self.id}] 业务失败", "error_type": "sub_agent_error",
                         "completed_steps": [f"{self.id}_step1"]},
                source=self.id, target=msg.source, thread_id=msg.thread_id,
            )
            return
        yield AgentMessage(type="response", action=msg.action, payload=payload,
                           source=self.id, target=msg.source, thread_id=msg.thread_id)


class TestSupervisor(SupervisorAgent):
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


async def drive_handle(supervisor, question="对比 A/B/C 三套实现方案，分别检索资料并产出文档"):
    """在线程主协程里直接驱动 supervisor 的 handle_message（相当于 endpoint→supervisor）。"""
    msg = AgentMessage(type="request", action="chat", payload={"question": question},
                       source="user", target="supervisor", thread_id="t-main")
    replies = []
    async for r in supervisor.handle_message(msg):
        replies.append(r)
    return replies


def elapse(factory_ok, serial_upper):
    """断言耗时证明并行的阈值：max_delay 是理论下限，serial_upper 是串行总和下限。"""


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------


async def scenario_bus_level():
    """[1] bus 层并发：3 个独立事件循环的 Agent 同时各睡 1s → 总耗时 ~1s 而非 ~3s。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 1.0, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 1.0, "网络答案"),
        "code": FakeSubAgent("code", 1.0, "代码答案"),
    }
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
        await asyncio.sleep(0.05)

    starts = [a.started_at for a in agents.values() if a.started_at]
    gap = max(starts) - min(starts) if starts else 99.0
    ok = 0.95 < elapsed < 2.0 and gap < 0.5
    return ok, f"3×1.0s 延迟，总耗时 {elapsed:.2f}s（串行应≈3.0s，并行应≈1.0s）；start 落点差 {gap:.2f}s"


async def scenario_supervisor_parallel():
    """[2][3] supervisor 分解→_execute_parallel：3 个子任务并行，且起始时刻互相重叠。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 0.8, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 0.8, "网络答案"),
        "code": FakeSubAgent("code", 0.8, "代码答案"),
    }
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    spv = TestSupervisor(bus, subtasks=[
        {"agent": "rag", "question": "Q1"},
        {"agent": "web_search", "question": "Q2"},
        {"agent": "code", "question": "Q3"},
    ])
    try:
        start = time.perf_counter()
        replies = await drive_handle(spv)
        elapsed = time.perf_counter() - start
        reply = replies[0]
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    starts = [a.started_at for a in agents.values() if a.started_at]
    gap = max(starts) - min(starts) if starts else 99.0
    ok = (
        reply.type == "response"
        and reply.payload["routed_to"] == "rag+web_search+code"
        and spv.synthesize_called
        and "并行汇总" in reply.payload["answer"]
        and 0.75 < elapsed < 1.7
        and gap < 0.5
    )
    return ok, (f"3×0.8s 延迟，总耗时 {elapsed:.2f}s（串行应≈2.4s）；"
                f"routed_to={reply.payload['routed_to']}；synthesize={spv.synthesize_called}；"
                f"start 落点差 {gap:.2f}s")


async def scenario_error_isolation():
    """[4] rag 成功、web_search 业务失败、code 崩溃 → 其余子 Agent 不受影响仍产出答案。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 0.6, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 0.6, "", outcome="error"),
        "code": FakeSubAgent("code", 0.6, "", outcome="raise"),
    }
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    spv = TestSupervisor(bus, subtasks=[
        {"agent": "rag", "question": "Q1"},
        {"agent": "web_search", "question": "Q2"},
        {"agent": "code", "question": "Q3"},
    ])
    try:
        start = time.perf_counter()
        replies = await drive_handle(spv)
        elapsed = time.perf_counter() - start
        reply = replies[0]
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    ok = (reply.type == "response" and reply.payload["routed_to"] == "rag"
          and reply.payload["answer"] == "KB 答案"
          and not spv.synthesize_called
          and 0.55 < elapsed < 1.4)
    return ok, (f"1 成功+1 失败+1 崩溃，总耗时 {elapsed:.2f}s；routed_to={reply.payload['routed_to']}；"
                f"answer={reply.payload['answer']!r}；gather 未抛异常（隔离生效）")


async def scenario_partial_failure_note():
    """[5] 2 成功 + 1 失败 → 多结果汇总，且 payload 带 ⚠️ 部分 Agent 执行出错 说明。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 0.5, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 0.5, "网络答案"),
        "code": FakeSubAgent("code", 0.5, "", outcome="error"),
    }
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    spv = TestSupervisor(bus, subtasks=[
        {"agent": "rag", "question": "Q1"},
        {"agent": "web_search", "question": "Q2"},
        {"agent": "code", "question": "Q3"},
    ])
    try:
        replies = await drive_handle(spv)
        reply = replies[0]
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    ok = (reply.type == "response" and spv.synthesize_called
          and reply.payload["routed_to"] == "rag+web_search"
          and "⚠️ 部分 Agent 执行出错" in reply.payload["answer"]
          and "web_search" in reply.payload["answer"] and "code" in reply.payload["answer"])
    return ok, f"routed_to={reply.payload['routed_to']}；answer 含错误说明={ok}"


async def scenario_graded_timeout():
    """[6] 不回复的 silent Agent：超时（含一次宽限续期）→ completed_steps 错误交付，不悬挂。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 0.4, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 0.4, "网络答案"),
        "code": FakeSubAgent("code", 30, "", outcome="silent"),
    }
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    spv = TestSupervisor(bus, subtasks=[
        {"agent": "rag", "question": "Q1"},
        {"agent": "web_search", "question": "Q2"},
        {"agent": "code", "question": "Q3"},
    ], timeout_override=0.5)
    try:
        start = time.perf_counter()
        replies = await drive_handle(spv)
        elapsed = time.perf_counter() - start
        reply = replies[0]
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    ok = (reply.type == "response" and "did not respond in time" in reply.payload["answer"]
          and "⚠️" in reply.payload["answer"] and "code" in reply.payload["answer"]
          and elapsed < 2.5)
    return ok, (f"code 不回复(30s)，总耗时 {elapsed:.2f}s（0.5s 超时 + 一次 0.5s 宽限）；"
                f"错误说明={ 'did not respond in time' in reply.payload['answer'] }")


async def scenario_single_route():
    """[7] 只有一个子任务 → 直接路由该 Agent，不调用 _synthesize。"""
    bus = AgentBus()
    agents = {"rag": FakeSubAgent("rag", 0.3, "单一答案")}
    for a in agents.values():
        bus.register(a)
    bus.start_all()
    spv = TestSupervisor(bus, subtasks=[{"agent": "rag", "question": "只拆出一个"}])
    try:
        replies = await drive_handle(spv)
        reply = replies[0]
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    ok = (reply.type == "response" and reply.payload["routed_to"] == "rag"
          and reply.payload["answer"] == "单一答案" and not spv.synthesize_called)
    return ok, f"routed_to={reply.payload['routed_to']}；answer={reply.payload['answer']!r}；synthesize 未调用={not spv.synthesize_called}"


async def main() -> int:
    scenarios = [
        ("bus 层并发（3 事件循环并行）", scenario_bus_level),
        ("supervisor 并行分解", scenario_supervisor_parallel),
        ("错误隔离（1 成功+1 失败+1 崩溃）", scenario_error_isolation),
        ("局部失败提示", scenario_partial_failure_note),
        ("分级超时+宽限续期", scenario_graded_timeout),
        ("单子任务直接路由", scenario_single_route),
    ]
    print("=" * 72)
    print("多 Agent 并发处理单问题 — 离线验证脚本")
    print("=" * 72)
    failed = 0
    for name, fn in scenarios:
        ok, detail = await fn()
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] {name}")
        print(f"        {detail}")
    print("=" * 72)
    if failed:
        print(f"结果：{len(scenarios) - failed}/{len(scenarios)} 通过，{failed} 失败")
        return 1
    print(f"结果：{len(scenarios)}/{len(scenarios)} 全部通过 —— 多 Agent 确实在并发处理同一个问题")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))