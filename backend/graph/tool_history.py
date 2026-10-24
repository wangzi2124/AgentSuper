"""Tool history carried across turns (Harness cross-model slice).

The graph keeps user/assistant messages in ``state.messages``. Tool calls and
their results are transient inside ``run_tool_loop`` today, so a fallback model
on the next turn never sees them. This module captures a small, bounded copy of
the most recent tool-bearing turn and makes it available to the next model call.

Design constraints:
- plain dicts only (no LangChain messages, no frontend history pollution);
- assistant(tool_calls) and tool results stay paired;
- at most 10 messages / 8 KB total / 2 KB per result / 1 KB per arguments;
- only the newest tool-bearing turn is kept, injected for at most 2 user turns.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

MAX_TOOL_MESSAGES = 10
MAX_TOTAL_CHARS = 8192
MAX_RESULT_CHARS = 2048
MAX_ARGS_CHARS = 1024
MAX_INJECT_TURNS = 2
_MIN_CONTENT_CHARS = 64


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def bounded_text(text: Any, limit: int = MAX_RESULT_CHARS) -> str:
    """Head+tail truncation; keeps both the beginning and the end."""
    value = _as_text(text)
    if limit <= 0 or len(value) <= limit:
        return value
    if limit <= 16:
        return value[:limit]
    marker = "\n... [truncated] ...\n"
    head = (limit - len(marker)) // 2
    tail = limit - len(marker) - head
    return value[:head] + marker + value[-tail:]


def _bounded_arguments(arguments: Any) -> str:
    value = _as_text(arguments) or "{}"
    if len(value) <= MAX_ARGS_CHARS:
        return value
    # Historical tool_calls must stay valid JSON for native providers.
    preview_len = max(1, MAX_ARGS_CHARS - 64)
    return json.dumps(
        {"_truncated": True, "preview": value[:preview_len]},
        ensure_ascii=False,
    )


def make_tool_call_message(
    calls: Iterable[tuple[str, str, Any]],
    content: str = "",
) -> dict[str, Any]:
    """Build the assistant message for one tool round.

    ``calls`` items are ``(call_id, name, arguments)``. Empty ids are allowed;
    callers should assign a stable fallback before calling.
    """
    return {
        "role": "assistant",
        "content": bounded_text(content) if content else "",
        "tool_calls": [
            {
                "id": str(call_id),
                "type": "function",
                "function": {
                    "name": str(name),
                    "arguments": _bounded_arguments(arguments),
                },
            }
            for call_id, name, arguments in calls
        ],
    }


def make_tool_result_message(
    call_id: str, name: str, output: Any
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": str(call_id),
        "name": str(name),
        "content": bounded_text(output),
    }


def _msg_role(msg: dict[str, Any]) -> str:
    return str(msg.get("role") or "")


def _message_cost(msg: dict[str, Any]) -> int:
    cost = len(_as_text(msg.get("content")))
    for call in msg.get("tool_calls") or []:
        if isinstance(call, dict):
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            cost += len(_as_text(fn.get("arguments")))
    return cost


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_message_cost(m) for m in messages)


def _split_rounds(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    rounds: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for msg in messages:
        if _msg_role(msg) == "assistant":
            current = [msg]
            rounds.append(current)
        elif current is None:
            current = [msg]
            rounds.append(current)
        else:
            current.append(msg)
    return rounds


def _trim_round(round_msgs: list[dict[str, Any]], slots: int) -> list[dict[str, Any]]:
    if slots <= 0:
        return []
    if len(round_msgs) <= slots:
        return list(round_msgs)
    assistant = round_msgs[0] if round_msgs and _msg_role(round_msgs[0]) == "assistant" else None
    if assistant is None:
        return list(round_msgs[-slots:])
    tools = [m for m in round_msgs if _msg_role(m) == "tool"]
    keep_tools = tools[: max(0, slots - 1)]
    kept_ids = {str(m.get("tool_call_id") or "") for m in keep_tools}
    calls = list(assistant.get("tool_calls") or [])
    if kept_ids:
        kept_calls = [
            c for c in calls if str((c or {}).get("id") or "") in kept_ids
        ]
    else:
        kept_calls = calls[: len(keep_tools)]
    trimmed = dict(assistant)
    trimmed["tool_calls"] = kept_calls
    return [trimmed] + keep_tools


def _flatten(rounds: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [m for r in rounds for m in r]


def _shrink_to_fit(messages: list[dict[str, Any]]) -> None:
    """Shrink tool result contents until the total budget is met."""
    while _total_chars(messages) > MAX_TOTAL_CHARS:
        candidates = [
            (idx, len(_as_text(m.get("content"))))
            for idx, m in enumerate(messages)
            if _msg_role(m) == "tool"
        ]
        candidates = [(i, n) for i, n in candidates if n > _MIN_CONTENT_CHARS]
        if not candidates:
            break
        idx, size = max(candidates, key=lambda item: item[1])
        limit = max(_MIN_CONTENT_CHARS, size // 2)
        messages[idx]["content"] = bounded_text(messages[idx].get("content"), limit)


def cap_tool_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound a tool-history list while keeping calls/results paired."""
    if not messages:
        return []
    normalized = [dict(m) for m in messages if isinstance(m, dict)]
    for msg in normalized:
        if "content" in msg:
            msg["content"] = bounded_text(msg.get("content"))
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict):
                fn = call.get("function")
                if isinstance(fn, dict):
                    fn["arguments"] = _bounded_arguments(fn.get("arguments"))
    rounds = _split_rounds(normalized)
    if not rounds:
        return []

    # Drop the oldest complete rounds first.
    while len(rounds) > 1 and (
        len(_flatten(rounds)) > MAX_TOOL_MESSAGES
        or _total_chars(_flatten(rounds)) > MAX_TOTAL_CHARS
    ):
        rounds.pop(0)

    # A single oversized round: keep the newest complete pairs that fit.
    kept: list[list[dict[str, Any]]] = []
    remaining = MAX_TOOL_MESSAGES
    for round_msgs in reversed(rounds):
        if remaining <= 0:
            break
        trimmed = _trim_round(round_msgs, remaining)
        if not trimmed:
            break
        kept.append(trimmed)
        remaining -= len(trimmed)
    rounds = list(reversed(kept))

    flattened = _flatten(rounds)
    _shrink_to_fit(flattened)
    return flattened


def merge_tool_history(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]] | None,
    *,
    existing_turn: int | None,
    new_turn: int | None,
    existing_anchor: str | None,
    new_anchor: str | None,
) -> tuple[list[dict[str, Any]], int | None, str | None]:
    """Merge one turn's tool history.

    Same user turn: append (multiple execute/subtask calls). New turn: replace.
    A turn without tool calls calls this with ``new=[]`` and gets old value back.
    """
    if not new:
        return list(existing or []), existing_turn, existing_anchor
    if existing and existing_turn is not None and new_turn == existing_turn:
        combined = list(existing) + list(new)
        anchor = existing_anchor or new_anchor
    else:
        combined = list(new)
        anchor = new_anchor
    return cap_tool_history(combined), new_turn, anchor


def _msg_type(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("type") or msg.get("role") or "")
    return str(getattr(msg, "type", None) or getattr(msg, "role", None) or "")


def human_count(messages: Iterable[Any]) -> int:
    return sum(1 for m in messages if _msg_type(m) in ("human", "user"))


def message_id(msg: Any) -> str | None:
    if isinstance(msg, dict):
        value = msg.get("id")
    else:
        value = getattr(msg, "id", None)
    return str(value) if value else None


def last_human_id(messages: Iterable[Any]) -> str | None:
    found: str | None = None
    for msg in messages:
        if _msg_type(msg) in ("human", "user"):
            found = message_id(msg) or found
    return found


def should_inject(tool_turn: int | None, current_turn: int) -> bool:
    if tool_turn is None or tool_turn <= 0:
        return False
    delta = current_turn - tool_turn
    return 0 < delta <= MAX_INJECT_TURNS


__all__ = [
    "MAX_ARGS_CHARS",
    "MAX_INJECT_TURNS",
    "MAX_RESULT_CHARS",
    "MAX_TOOL_MESSAGES",
    "MAX_TOTAL_CHARS",
    "bounded_text",
    "cap_tool_history",
    "human_count",
    "last_human_id",
    "make_tool_call_message",
    "make_tool_result_message",
    "merge_tool_history",
    "message_id",
    "should_inject",
]
