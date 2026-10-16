"""任务分解节点（对齐 agent.md §3.2 / §7.5.1）。

无模型时（M1 快速路径）：把用户请求转成单步计划。
注入 `plan_model` 时（M3）：用 LLM 分解为结构化子任务（含 agent 归属），
供并行扇出与委派使用。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from prompts.loader import PromptLoader

from ..tasks import KNOWN_AGENTS, parse_task_specs
from ..usage import usage_from_result
from .base import content_text

logger = logging.getLogger(__name__)

MAX_TASKS = 5


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        role = getattr(msg, "type", None)
        if role in ("human", "user"):
            return content_text(getattr(msg, "content", ""))
    messages = state.get("messages") or []
    if messages:
        return content_text(getattr(messages[-1], "content", ""))
    return ""


def _single(text: str) -> list[dict[str, Any]]:
    return [{"id": "t1", "desc": text or "完成用户请求", "status": "pending"}]


def make_plan_node(
    model: Any | None = None,
    max_tasks: int = MAX_TASKS,
) -> Callable[[dict[str, Any]], Any]:
    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("task_plan"):
            return {}

        request = _last_user_text(state)
        if model is None:
            return {"task_plan": _single(request)}

        usage: dict[str, Any] = {}
        try:
            prompt = PromptLoader().load("nodes/plan.md", content=request)
            res = await model.ainvoke([{"role": "user", "content": prompt}])
            usage = usage_from_result(res, getattr(model, "tier", None))
            specs = parse_task_specs(getattr(res, "content", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("plan decomposition failed, using single task: %s", exc)
            specs = []

        if not specs:
            return {"task_plan": _single(request), "token_usage": usage}

        tasks = [
            {
                "id": s.id,
                "desc": s.objective,
                "agent": s.agent,
                "context": s.context,
                "deliverable": s.deliverable,
                "status": "pending",
            }
            for s in specs[:max_tasks]
            if s.agent in KNOWN_AGENTS
        ]
        if not tasks:
            return {"task_plan": _single(request), "token_usage": usage}
        return {"task_plan": tasks, "token_usage": usage}

    return plan_node
