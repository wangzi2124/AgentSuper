from __future__ import annotations

from typing import Any

from app.events.emitter import emit_event
from app.session import repository as repo
from app.session.context import current_platform_ctx, current_session_id
from app.session.plan import new_plan_id, normalize_todos, plan_event_data

TODO_TOOL_NAME = "todo_write"

TODO_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "description": "完整计划快照；每项含 title、可选 id/status/agent。status 为 pending/in_progress/completed/cancelled。",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "稳定步骤 id；省略时按标题复用或自动生成。"},
                    "title": {"type": "string", "description": "步骤标题。"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                        "description": "步骤状态；默认 pending。",
                    },
                    "agent": {"type": "string", "description": "可选执行 agent 名称。"},
                },
                "required": ["title"],
            },
        },
        "plan_id": {
            "type": "string",
            "description": "可选；省略时更新本会话当前计划，否则创建新计划。",
        },
        "tier": {"type": "string", "description": "可选计划档位：single/serial/parallel。"},
    },
    "required": ["todos"],
}


async def todo_write(
    todos: Any = "",
    plan_id: str = "",
    tier: str = "",
) -> str:
    """创建或更新会话 todo 列表；传入完整步骤快照，id 与 revision 由系统维护。

    仅在任务确有多个步骤或需要显式跟踪进度时调用。状态变更要有真实依据，
    不要在未验证步骤完成时标记 completed。不要把工具结果当作用户回复。
    """
    session_id = current_session_id()
    if not session_id:
        return "错误：当前没有活动会话，无法写入 todo"

    requested = str(plan_id or "").strip()
    existing = repo.get_plan(session_id, requested or None)
    if existing is None and requested:
        existing = repo.get_plan(session_id)
        resolved_id = requested
    elif existing is not None:
        resolved_id = str(existing.get("plan_id") or requested)
    else:
        resolved_id = requested or new_plan_id()

    previous = list((existing or {}).get("steps") or [])
    steps = normalize_todos(todos, previous)
    if not steps:
        return "错误：todo 列表为空或格式无效"
    revision = int((existing or {}).get("revision") or 0) + 1
    resolved_tier = str(tier or (existing or {}).get("tier") or "").strip() or None

    saved = repo.save_plan(session_id, resolved_id, revision, steps, resolved_tier)
    if not saved:
        return f"错误：todo revision {revision} 已过期，请重新读取后再写"

    ctx = current_platform_ctx()
    if ctx is not None:
        await emit_event(
            ctx,
            "session.plan.updated",
            plan_event_data(session_id, resolved_id, revision, steps, resolved_tier),
            session_id=session_id,
        )
    lines = [f"{step['status']}: {step['title']}" for step in steps]
    return f"todo 已保存（revision {revision}）：\n" + "\n".join(lines)


def make_todo_tools() -> dict[str, Any]:
    return {TODO_TOOL_NAME: todo_write}


__all__ = [
    "TODO_TOOL_NAME",
    "TODO_TOOL_PARAMETERS",
    "make_todo_tools",
    "todo_write",
]
