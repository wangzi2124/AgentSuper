"""条件路由（对齐 agent.md §3.3）。"""

from __future__ import annotations

from typing import Any

from .nodes.base import content_text, has_image_content

MAX_ITERATIONS = 25
COMPRESS_THRESHOLD = 80_000


def _last_content(state: dict[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""
    return content_text(getattr(messages[-1], "content", ""))


def _has_image(state: dict[str, Any]) -> bool:
    """本回合用户消息是否带图片分块。"""
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            return has_image_content(getattr(msg, "content", ""))
    return False


def route_after_supervisor(state: dict[str, Any]) -> str:
    """Supervisor 根据当前状态决定下一步。"""

    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return "synthesize"

    if state.get("final_output"):
        return "end"

    if state.get("token_usage", {}).get("total_tokens", 0) > COMPRESS_THRESHOLD:
        return "compress"

    plan = state.get("task_plan") or []
    pending = [t for t in plan if t.get("status") != "done"]

    if not plan:
        last = _last_content(state)
        if "[NEED_RAG]" in last:
            return "rag"
        if "[NEED_MEMORY]" in last:
            return "recall"
        return "plan"

    if pending:
        return "execute"

    if state.get("sub_results") or state.get("draft_output"):
        return "synthesize"

    return "synthesize"


def route_after_plan(state: dict[str, Any]) -> str:
    """计划完成后：多个带 agent 归属的子任务 → 并行扇出，否则单步执行。

    含图片的本回合**始终**走 `execute_subagent`（主 Agent 的视觉模型能看图）：
    子 Agent 上下文隔离、只拿结构化文本任务（§5.4），图片分块到不了它们手上，
    会让模型对着「看不到图」的指令硬猜（实测同一张蓝图时对时错）。
    """
    if _has_image(state):
        return "execute_subagent"
    pending = [t for t in (state.get("task_plan") or []) if t.get("status") != "done"]
    agent_tasks = [t for t in pending if t.get("agent")]
    if len(agent_tasks) > 1:
        return "parallel"
    return "execute_subagent"
