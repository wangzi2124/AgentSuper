"""并行扇出节点（对齐 agent.md §5.2 / §12 M3）。

当计划包含多个由子 Agent 承担的子任务时，用 `asyncio.gather` 并发执行
（受 `max_concurrency` 信号量约束），再合并各自结果。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..usage import merge_usage

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 3


def make_parallel_node(
    runner: Callable[..., Any],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> Callable[[dict[str, Any]], Any]:
    """构造并行执行节点。runner 签名 `async (agent, desc, context) -> dict`。"""

    async def parallel_node(state: dict[str, Any]) -> dict[str, Any]:
        plan = [dict(t) for t in (state.get("task_plan") or [])]
        pending = [t for t in plan if t.get("status") != "done"]
        if not pending:
            return {}

        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def run_one(t: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
            tid = t.get("id", "t?")
            agent = t.get("agent") or "file"
            desc = t.get("desc") or t.get("objective") or ""
            context = t.get("context") or ""
            async with sem:
                try:
                    res = await runner(agent, desc, context)
                    if isinstance(res, dict):
                        return tid, res.get("text", ""), res.get("usage") or {}
                    return tid, str(res), {}
                except Exception as exc:  # noqa: BLE001
                    logger.warning("subagent %s failed: %s", agent, exc)
                    return tid, f"(子任务 {tid} 失败：{exc})", {}

        results = await asyncio.gather(*(run_one(t) for t in pending))

        sub_results = dict(state.get("sub_results") or {})
        done_ids = set()
        usage: dict[str, Any] = {}
        for tid, text, sub_usage in results:
            sub_results[tid] = text
            done_ids.add(tid)
            usage = merge_usage(usage, sub_usage)
        for t in plan:
            if t.get("id") in done_ids:
                t["status"] = "done"

        return {
            "task_plan": plan,
            "sub_results": sub_results,
            "draft_output": "\n\n".join(
                f"### 子任务 {tid}\n{text}" for tid, text, _ in results
            ),
            "token_usage": usage,
        }

    return parallel_node


def make_parallel_worker_node(runner: Callable[..., Any]) -> Callable[[dict[str, Any]], Any]:
    """`Send` 动态扇出 worker（agent-1.md §2.3）。

    `route_after_plan` 为每个子任务 `Send("parallel_worker", {task_id, agent,
    desc, context})`，LangGraph 并发运行本节点多次；每次只跑一个子任务并返回
    增量 `{"sub_results": {tid: text}}`（由 `merge_dicts` reducer 合并）。
    `fanout_count` 用数值 reducer 累加扇出次数（§2.4 C2）。
    """

    async def parallel_worker(payload: dict[str, Any]) -> dict[str, Any]:
        tid = payload.get("task_id", "t?")
        agent = payload.get("agent") or "file"
        desc = payload.get("desc") or ""
        context = payload.get("context") or ""
        usage: dict[str, Any] = {}
        try:
            res = await runner(agent, desc, context)
            if isinstance(res, dict):
                text = res.get("text", "")
                usage = res.get("usage") or {}
            else:
                text = str(res)
        except Exception as exc:  # noqa: BLE001
            logger.warning("subagent %s failed: %s", agent, exc)
            text = f"(子任务 {tid} 失败：{exc})"
        return {
            "sub_results": {tid: text},
            "token_usage": usage,
            "fanout_count": 1,
        }

    return parallel_worker
