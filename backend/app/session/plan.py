from __future__ import annotations

import re
from typing import Any

CONTRACT_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
MAX_TODOS = 12

_INTERNAL_TO_CONTRACT = {
    "done": "completed",
    "completed": "completed",
    "cancelled": "cancelled",
    "running": "in_progress",
    "in_progress": "in_progress",
    "pending": "pending",
}
_CONTRACT_TO_INTERNAL = {
    "pending": "pending",
    "in_progress": "pending",
    "completed": "done",
    "cancelled": "done",
}


def new_plan_id(turn_id: str = "") -> str:
    return f"plan_{turn_id}" if turn_id else "plan_local"


def steps_from_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        status = str(task.get("status") or "pending")
        steps.append(
            {
                "id": str(task.get("id") or f"t{index + 1}"),
                "title": str(task.get("desc") or task.get("title") or task.get("objective") or ""),
                "status": _INTERNAL_TO_CONTRACT.get(status, "pending"),
                "agent": str(task.get("agent") or "") or None,
            }
        )
    return steps


def tasks_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        status = str(step.get("status") or "pending")
        tasks.append(
            {
                "id": str(step.get("id") or f"t{index + 1}"),
                "desc": str(step.get("title") or ""),
                "status": _CONTRACT_TO_INTERNAL.get(status, "pending"),
                "agent": str(step.get("agent") or "") or None,
            }
        )
    return tasks


def normalize_todos(
    raw: Any,
    previous: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if raw is None:
        items: list[Any] = []
    elif isinstance(raw, str):
        items = [part.strip() for part in re.split(r"[\n,;]+", raw) if part.strip()]
    elif isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []

    previous_by_id = {
        str(step.get("id") or ""): step for step in (previous or []) if step.get("id")
    }
    previous_by_title = {
        str(step.get("title") or ""): step for step in (previous or []) if step.get("title")
    }
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(items[:MAX_TODOS]):
        data = item if isinstance(item, dict) else {}
        title = str(
            data.get("title") or data.get("desc") or data.get("text") or item or ""
        ).strip()[:200]
        if not title:
            continue
        status = str(data.get("status") or "pending").strip().lower()
        if status not in CONTRACT_STATUSES:
            status = "pending"
        provided_id = str(data.get("id") or "").strip()
        existing = previous_by_id.get(provided_id) or previous_by_title.get(title)
        step_id = provided_id or str(existing.get("id") or "") if existing else provided_id
        step_id = step_id or f"s{index + 1}"
        agent = str(data.get("agent") or "").strip() or None
        steps.append({"id": step_id, "title": title, "status": status, "agent": agent})
    return steps


def plan_event_data(
    session_id: str,
    plan_id: str,
    revision: int,
    steps: list[dict[str, Any]],
    tier: str | None = None,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "plan_id": plan_id,
        "revision": int(revision),
        "tier": tier or None,
        "steps": steps,
    }
