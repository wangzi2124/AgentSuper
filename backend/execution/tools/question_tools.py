"""Durable question tool backed by session forms (opencode gap G-15).

The model turns "I am not sure what to do next" into a structured choice with a
recommended default instead of guessing. Answers are persisted in
``session_forms`` and wake the waiting tool call through ``wake_form``.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from typing import Any

from app.session import repository as repo
from app.session.context import current_platform_ctx, current_session_id

QUESTION_TOOL_NAME = "ask_question"
MAX_OPTIONS = 8
_RECOMMENDED_SUFFIX = re.compile(
    r"\s*[（(](?:recommended|推荐|建议|recommend)[）)]\s*$", re.IGNORECASE
)

QUESTION_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "需要用户决策的单一问题；只包含真正阻塞下一步的内容。",
        },
        "header": {"type": "string", "description": "短标签，如「实现方向」。"},
        "options": {
            "type": "array",
            "description": "2–4 个互斥选项；有推荐项时把它放第一位并置 recommended=true。",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "选项标题。"},
                    "description": {"type": "string", "description": "一句话说明选择后的影响。"},
                    "recommended": {
                        "type": "boolean",
                        "description": "是否为推荐默认项；若未提供则第一项视为推荐。",
                    },
                },
                "required": ["label"],
            },
        },
        "multiple": {"type": "boolean", "description": "是否允许选择多个选项。"},
        "custom": {"type": "boolean", "description": "是否允许「自定义答案」文本输入。"},
        "timeout_seconds": {"type": "integer", "description": "最长等待秒数（默认 300）。"},
    },
    "required": ["question", "options"],
}


class _QuestionWaiters:
    """Turn-scoped in-process wakeup; DB polling remains the cross-process fallback."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._lock = threading.Lock()

    def register(self, form_id: str) -> asyncio.Event:
        event = asyncio.Event()
        with self._lock:
            self._events[form_id] = event
        return event

    def wake(self, form_id: str) -> None:
        with self._lock:
            event = self._events.get(form_id)
        if event is not None:
            event.set()

    def discard(self, form_id: str) -> None:
        with self._lock:
            self._events.pop(form_id, None)


_waiters = _QuestionWaiters()


def wake_form(form_id: str) -> None:
    """Wake a blocked ``ask_question`` call after a reply/cancel was persisted."""
    _waiters.wake(form_id)


def normalize_options(options: Any) -> list[dict[str, Any]]:
    """Normalise model options (structured dicts or legacy comma string)."""
    if options is None:
        raw: list[Any] = []
    elif isinstance(options, str):
        raw = [part.strip() for part in options.split(",") if part.strip()]
    elif isinstance(options, (list, tuple)):
        raw = list(options)
    elif isinstance(options, dict):
        raw = [options]
    else:
        raw = []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:MAX_OPTIONS]:
        if isinstance(item, dict):
            label = str(
                item.get("label") or item.get("name") or item.get("value") or ""
            ).strip()
            description = str(item.get("description") or item.get("desc") or "").strip()
            recommended = bool(item.get("recommended", item.get("recommend", False)))
        else:
            label = str(item or "").strip()
            description = ""
            recommended = False
        if not label or label in seen:
            continue
        seen.add(label)
        suffix = _RECOMMENDED_SUFFIX.search(label)
        if suffix:
            recommended = True
            label = label[: suffix.start()].strip()
        normalized.append(
            {"label": label, "description": description, "recommended": recommended}
        )

    if normalized and not any(option["recommended"] for option in normalized):
        normalized[0]["recommended"] = True
    return sorted(normalized, key=lambda option: not option["recommended"])



async def _emit_form_event(event_type: str, data: dict[str, Any], session_id: str) -> None:
    ctx = current_platform_ctx()
    if ctx is None:
        return
    try:
        from app.events.emitter import emit_event

        await emit_event(ctx, event_type, data, session_id=session_id)
    except Exception:  # noqa: BLE001 - the form is durable and remains pollable
        pass


def _answer_text(response: dict[str, Any] | None) -> str:
    answer = (response or {}).get("answer")
    if isinstance(answer, (list, tuple)):
        return ", ".join(str(item) for item in answer)
    return str(answer or "")


async def ask_question(
    question: str,
    options: Any = "",
    header: str = "",
    multiple: bool = False,
    custom: bool = True,
    timeout_seconds: int = 300,
) -> str:
    """向用户提出结构化选择题并等待回答（会暂停本回合）。

    仅在真正无法安全选择默认值、用户决策会改变下一步时才调用；能通过工具
    查证或采用合理默认值时继续推进，不打断用户。一次只问一个关键问题。

    Args:
        question: 单一、明确的问题文本。
        options: 选项列表，每项为 ``{label, description, recommended}``；
            兼容逗号分隔字符串。2–4 项为佳；推荐项放第一位并置
            ``recommended=true``，label 也可带 ``(Recommended)`` 后缀。
            未提供推荐标记时第一项自动视为推荐。
        header: 短标签（如「实现方向」），用于卡片标题旁的小标题。
        multiple: 是否允许多选。
        custom: 是否允许用户在选项之外输入自定义答案。
        timeout_seconds: 最长等待秒数（默认 300，上限 1800）。

    Returns:
        用户选择的答案文本；用户取消返回取消提示，超时返回超时提示。
    """
    session_id = current_session_id()
    if not session_id:
        return "错误：当前没有活动会话，无法提问"

    details = normalize_options(options)
    field: dict[str, Any] = {
        "id": "answer",
        "type": "select" if details else "text",
        "label": str(question or "请回答"),
        "required": True,
        "options": [option["label"] for option in details],
        "option_details": details,
        "multiple": bool(multiple) and bool(details),
        "allow_custom": bool(custom) if details else True,
    }
    form_id = f"form_{uuid.uuid4().hex[:12]}"
    created = repo.create_form(session_id, form_id, [field], title=str(question or "")[:120])
    info = {
        "id": form_id,
        "session_id": session_id,
        "title": str(question or "")[:120],
        "header": str(header or "")[:60],
        "question": str(question or "")[:120],
        "kind": "question",
        "fields": [field],
        "state": "pending",
        "response": {},
        "revision": int(created.get("revision", 0)),
    }
    event = _waiters.register(form_id)
    await _emit_form_event("form.created", info, session_id)

    deadline = time.monotonic() + max(1, min(int(timeout_seconds or 300), 1800))
    try:
        while True:
            for item in repo.list_forms(session_id):
                if item["id"] != form_id:
                    continue
                if item["state"] == "replied":
                    answer = _answer_text(item.get("response"))
                    return answer or "用户未填写答案"
                if item["state"] == "cancelled":
                    return "用户取消了该问题"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                repo.cancel_form(session_id, form_id)
                await _emit_form_event(
                    "form.cancelled",
                    {"session_id": session_id, "form_id": form_id, "revision": 1},
                    session_id,
                )
                return "等待用户回答超时"
            try:
                await asyncio.wait_for(event.wait(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                continue
            event.clear()
    finally:
        _waiters.discard(form_id)


def make_question_tools() -> dict[str, Any]:
    return {QUESTION_TOOL_NAME: ask_question}


__all__ = [
    "QUESTION_TOOL_NAME",
    "QUESTION_TOOL_PARAMETERS",
    "ask_question",
    "make_question_tools",
    "normalize_options",
    "wake_form",
]
