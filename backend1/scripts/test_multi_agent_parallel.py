#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AgentBus 并发 + 分级超时的可执行验证脚本（离线，无需真实 LLM / 知识库）。

用法（Windows，直接用 backend/.venv 的解释器）：
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\test_multi_agent_parallel.py

[C4 · 2026-09-29] 原脚本的 [2][3][5][7] 场景依赖 supervisor 的并行分解
（`_execute_parallel` / `_synthesize` / 多子任务错误隔离）—— 该实现恒不可达，
已随并行分解一并删除，故这些场景不再成立。保留并重写的是仍然有效的保障：

  [1][2] bus 层并发 —— 3 路 gather 的 send_and_wait 总耗时 ≈ max(延迟)
                      （串行会是 3×延迟），且三个 Agent 的起始时刻重叠；
  [3] 错误隔离     —— 单个 Agent 崩溃 / 业务失败不影响其余 Agent 结果；
  [4] 分级超时+宽限 —— 不回复的 Agent 走 send_and_wait 超时 → 按期抛
                      TimeoutError（消息含真实最坏等待），不悬挂整个请求。

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


def start_gap(agents: dict) -> float:
    starts = [a.started_at for a in agents.values() if a.started_at]
    return max(starts) - min(starts) if starts else 99.0


def build(bus: AgentBus, agents: dict) -> None:
    for a in agents.values():
        bus.register(a)
    bus.start_all()


async def scenario_bus_level():
    """[1][2] 3 个 Agent 同时各睡 1s → 总耗时 ~1s 而非 ~3s，且起始时刻重叠。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 1.0, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 1.0, "网络答案"),
        "code": FakeSubAgent("code", 1.0, "代码答案"),
    }
    build(bus, agents)
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
        await asyncio.sleep(0.05)

    gap = start_gap(agents)
    answers = {r.payload["answer"] for r in results}
    ok = 0.95 < elapsed < 2.0 and gap < 0.5 and answers == {"KB 答案", "网络答案", "代码答案"}
    return ok, (f"3×1.0s 延迟，总耗时 {elapsed:.2f}s（串行应≈3.0s，并行应≈1.0s）；"
                f"start 落点差 {gap:.2f}s；答案集={sorted(answers)}")


async def scenario_error_isolation():
    """[3] 一个成功、一个业务失败、一个崩溃 → 各自按类型交付，其余不受影响。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 0.6, "KB 答案"),
        "web_search": FakeSubAgent("web_search", 0.6, "", outcome="error"),
        "code": FakeSubAgent("code", 0.6, "", outcome="raise"),
    }
    build(bus, agents)
    try:
        start = time.perf_counter()
        replies = await asyncio.gather(*[
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": f"Q{i}"},
                             source="user", target=aid, thread_id=f"bus-{i}"),
                timeout=5,
            ) for i, aid in enumerate(agents)
        ], return_exceptions=True)
        elapsed = time.perf_counter() - start
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    by_target = {r.source: r for r in replies if not isinstance(r, BaseException)}
    ok = (
        by_target.get("rag") is not None and by_target["rag"].payload["answer"] == "KB 答案"
        and by_target.get("web_search") is not None
        and by_target["web_search"].type == "error"
        and "业务失败" in by_target["web_search"].payload["error"]
        and by_target.get("code") is not None
        and by_target["code"].type == "error"
        and "crashed" in by_target["code"].payload["error"]
        and 0.55 < elapsed < 1.4
    )
    return ok, (f"总耗时 {elapsed:.2f}s；success/error/crash 三路均按类型交付，"
                f"未互相污染（隔离生效）")


async def scenario_graded_timeout():
    """[4] silent Agent：超时（含一次有限宽限）→ 按期抛 TimeoutError，不悬挂。"""
    bus = AgentBus()
    agents = {
        "rag": FakeSubAgent("rag", 0.4, "KB 答案"),
        "silent": FakeSubAgent("silent", 30, "", outcome="silent"),
    }
    build(bus, agents)
    try:
        start = time.perf_counter()
        results = await asyncio.gather(
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": "Q1"},
                             source="user", target="rag", thread_id="bus-0"),
                timeout=5,
            ),
            bus.send_and_wait(
                AgentMessage(type="request", action="chat", payload={"question": "Q2"},
                             source="user", target="silent", thread_id="bus-1"),
                timeout=0.5,
            ),
            return_exceptions=True,
        )
        elapsed = time.perf_counter() - start
    finally:
        bus.stop_all()
        await asyncio.sleep(0.05)

    ok_reply, timeout_exc = results
    timed_out = isinstance(timeout_exc, asyncio.TimeoutError)
    detail = str(timeout_exc) if timed_out else repr(timeout_exc)
    ok = (
        timed_out
        and not isinstance(ok_reply, BaseException)
        and ok_reply.payload["answer"] == "KB 答案"
        # [C9] 超时消息写出真实最坏等待 = 基础超时 + 一次有限宽限（不再是静默翻倍）
        and "max_with_grace=" in detail
        and elapsed < 2.5
    )
    return ok, (f"silent 不回复(30s)：总耗时 {elapsed:.2f}s（0.5s 超时 + 一次 ≤0.5s 宽限）；"
                f"异常={detail}；并行路正常返回={ok_reply.payload.get('answer')!r}")


async def main() -> int:
    scenarios = [
        ("bus 层并发（3 Agent 并行 + 起始重叠）", scenario_bus_level),
        ("错误隔离（1 成功+1 失败+1 崩溃）", scenario_error_isolation),
        ("分级超时+有限宽限", scenario_graded_timeout),
    ]
    print("=" * 72)
    print("AgentBus 并发 + 分级超时 — 离线验证脚本")
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
    print(f"结果：{len(scenarios)}/{len(scenarios)} 全部通过 —— 总线确实并发派发子任务")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
