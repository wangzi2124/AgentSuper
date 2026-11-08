"""Durable question tool backed by session forms (opencode gap G-15)."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from app.session import repository as repo
from app.session.context import current_platform_ctx, current_session_id


async def ask_question(
    question: str,
    options: str = "",
    timeout_seconds: int = 300,
) -> str:
    """向用户提出一个问题并等待回答（会暂停本回合，直到回答或超时）。

    Args:
        question: 要询问用户的问题。
        options: 可选项，逗号分隔；为空时使用文本输入。
        timeout_seconds: 最长等待秒数（默认 300）。
    """
    session_id = current_session_id()
    if not session_id:
        return "错误：当前没有活动会话，无法提问"
    option_list = [item.strip() for item in str(options or "").split(",") if item.strip()]
    field_type = "select" if option_list else "text"
    form_id = f"form_{uuid.uuid4().hex[:12]}"
    fields = [
        {
            "id": "answer",
            "type": field_type,
            "label": str(question or "请回答"),
            "required": True,
            "options": option_list,
        }
    ]
    form = repo.create_form(session_id, form_id, fields, title=str(question or "")[:120])
    ctx = current_platform_ctx()
    if ctx is not None:
        try:
            from app.events.emitter import emit_event

            await emit_event(
                ctx,
                "form.created",
                {
                    "id": form["id"],
                    "session_id": form["session_id"],
                    "title": form["title"],
                    "fields": fields,
                    "state": "pending",
                    "response": {},
                },
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001 - forms still work via polling
            pass

    deadline = time.monotonic() + max(1, min(int(timeout_seconds), 1800))
    while time.monotonic() < deadline:
        for item in repo.list_forms(session_id):
            if item["id"] != form_id:
                continue
            if item["state"] == "replied":
                answer = (item.get("response") or {}).get("answer") or item.get("response") or {}
                return str(answer)
            if item["state"] == "cancelled":
                return "用户取消了该问题"
        await asyncio.sleep(0.5)
    repo.cancel_form(session_id, form_id)
    return "等待用户回答超时"


def make_question_tools() -> dict[str, Any]:
    return {"ask_question": ask_question}


__all__ = ["ask_question", "make_question_tools"]
