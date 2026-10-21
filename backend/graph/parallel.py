"""并行扇出图（对齐 agent.md §6.2）。

`build_parallel_graph()` 把一批**相互独立**的子任务按 agent 归属分发到
research / code / file 三个 worker 节点并发执行（LangGraph 同一步多出边即并行），
再由 merge 节点汇聚。

适用条件（§6.2）：子任务之间无数据依赖、输入可完全自包含、合并逻辑明确。
有顺序依赖的子任务应留在主图串行执行（`graph/nodes/parallel.py` 的动态并发节点）。

设计说明：LangGraph 中同一超步内多个节点写入同一 state 键会冲突，故本图用
带 reducer 的 `ParallelState`（`sub_results` 合并字典、`draft_output` 拼接文本），
保证并发写入安全。worker 只依赖注入的 `runner`（同 `graph/subagent.make_runner`
契约：`async (agent, task, context) -> dict`），便于测试时注入假 runner。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from .usage import merge_dicts as _merge_dict, merge_usage

logger = logging.getLogger(__name__)

WORKER_AGENTS: tuple[str, ...] = ("research", "code", "file")
DEFAULT_MAX_CONCURRENCY = 3


def _concat_text(old: str | None, new: str | None) -> str:
    if not old:
        return new or ""
    if not new:
        return old
    return f"{old}\n\n{new}"


class ParallelState(TypedDict, total=False):
    """并行扇出图状态（并发写入键均带 reducer）。"""

    messages: Annotated[list[BaseMessage], add_messages]
    task_plan: list[dict[str, Any]]
    dispatch_plan: dict[str, list[dict[str, Any]]]
    sub_results: Annotated[dict[str, str], _merge_dict]
    draft_output: Annotated[str, _concat_text]
    final_output: str
    token_usage: Annotated[dict[str, Any], merge_usage]


def split_by_agent(
    tasks: list[dict[str, Any]], agents: tuple[str, ...] = WORKER_AGENTS
) -> dict[str, list[dict[str, Any]]]:
    """把待执行子任务按 `agent` 字段分桶（未知 agent 归入 file）。"""
    buckets: dict[str, list[dict[str, Any]]] = {a: [] for a in agents}
    for t in tasks:
        if t.get("status") == "done":
            continue
        agent = t.get("agent") or "file"
        if agent not in buckets:
            agent = "file"
        buckets[agent].append(t)
    return buckets


def make_dispatch_node(
    agents: tuple[str, ...] = WORKER_AGENTS,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """dispatch：把 task_plan 中待执行子任务按 agent 分桶，供三个 worker 并行读取。"""

    def dispatch_node(state: dict[str, Any]) -> dict[str, Any]:
        plan = state.get("task_plan") or []
        pending = [t for t in plan if t.get("status") != "done"]
        if not pending:
            last = state.get("messages") or []
            content = getattr(last[-1], "content", "") if last else ""
            pending = [{"id": "t1", "agent": "file", "objective": str(content)}]
        return {"dispatch_plan": split_by_agent(pending, agents)}

    return dispatch_node


def make_worker_node(
    agent: str,
    runner: Callable[..., Any],
) -> Callable[[dict[str, Any]], Any]:
    """构造单个 worker 节点：串行执行本 agent 的所有分派子任务。"""

    async def worker_node(state: dict[str, Any]) -> dict[str, Any]:
        tasks = (state.get("dispatch_plan") or {}).get(agent) or []
        if not tasks:
            return {}
        results: dict[str, str] = {}
        usage: dict[str, Any] = {}
        for t in tasks:
            tid = t.get("id", "t?")
            desc = t.get("objective") or t.get("desc") or ""
            context = t.get("context") or ""
            try:
                res = await runner(agent, desc, context)
                if isinstance(res, dict):
                    text = res.get("text", "")
                    usage = merge_usage(usage, res.get("usage") or {})
                else:
                    text = str(res)
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker %s failed on %s: %s", agent, tid, exc)
                text = f"(子任务 {tid} 失败：{exc})"
            results[tid] = text
        return {"sub_results": results, "token_usage": usage}

    return worker_node


def make_merge_node(
    agents: tuple[str, ...] = WORKER_AGENTS,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """merge：汇总各 worker 结果，标记 task_plan 完成，生成 draft_output。"""

    def merge_node(state: dict[str, Any]) -> dict[str, Any]:
        sub_results = dict(state.get("sub_results") or {})
        plan = [dict(t) for t in (state.get("task_plan") or [])]
        for t in plan:
            if t.get("id") in sub_results:
                t["status"] = "done"
        # 保证所有分派子任务都有落地结果（防止个别 worker 静默丢结果）
        for bucket in (state.get("dispatch_plan") or {}).values():
            for t in bucket:
                tid = t.get("id", "t?")
                sub_results.setdefault(tid, "")
        draft = "\n\n".join(
            f"### 子任务 {tid}\n{text}" for tid, text in sub_results.items()
        )
        return {"task_plan": plan, "sub_results": sub_results, "draft_output": draft}

    return merge_node


def build_parallel_graph(
    *,
    runner: Callable[..., Any] | None = None,
    registry: Any | None = None,
    model_factory: Callable[[Any], Any] | None = None,
    vfs: Any | None = None,
    agents: tuple[str, ...] = WORKER_AGENTS,
) -> Any:
    """构建并行扇出图（§6.2）。不传 `runner` 时按 `registry`/`vfs` 现造子 Agent runner。"""
    if runner is None:
        from .main_graph import default_registry
        from .subagent import make_runner

        if registry is None:
            registry, _ = default_registry(vfs)
        runner = make_runner(registry, model_factory=model_factory)

    builder = StateGraph(ParallelState)
    builder.add_node("dispatch", make_dispatch_node(agents))
    for agent in agents:
        builder.add_node(f"{agent}_worker", make_worker_node(agent, runner))
    builder.add_node("merge", make_merge_node(agents))

    builder.set_entry_point("dispatch")
    for agent in agents:
        builder.add_edge("dispatch", f"{agent}_worker")
        builder.add_edge(f"{agent}_worker", "merge")
    builder.add_edge("merge", END)

    return builder.compile()
