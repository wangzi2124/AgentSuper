"""执行节点（对齐 agent.md §3.2 / §6.1）。

取出计划中下一个待办子任务，交给带工具的 LLM 循环执行；
结果写入 sub_results，并把任务标记为 done。
"""

from __future__ import annotations

from typing import Any, Callable

from execution.tool_registry import ToolRegistry
from models.router import TierModel

from .base import run_tool_loop

TOOL_SCOPE = "location"


def make_execute_node(
    model: TierModel,
    registry: ToolRegistry,
    system_prompt: str = "",
    scope: str | None = TOOL_SCOPE,
) -> Callable[[dict[str, Any]], Any]:
    async def execute_node(state: dict[str, Any]) -> dict[str, Any]:
        plan = [dict(t) for t in (state.get("task_plan") or [])]
        pending = [t for t in plan if t.get("status") != "done"]
        if not pending:
            return {}

        task = pending[0]
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": task.get("desc", "")})

        result = await run_tool_loop(model, messages, registry, scope=scope)

        task_id = task.get("id", "t?")
        for t in plan:
            if t.get("id") == task_id:
                t["status"] = "done"
        sub_results = dict(state.get("sub_results") or {})
        sub_results[task_id] = result["text"]
        return {
            "task_plan": plan,
            "current_task_id": task_id,
            "sub_results": sub_results,
            "draft_output": result["text"],
            "tool_rounds": state.get("tool_rounds", 0) + result["rounds"],
            "token_usage": result.get("usage") or {},
        }

    return execute_node
