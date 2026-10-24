"""ContextHarness: assemble the model-visible history.

This is the pure core behind ``graph.nodes.execute._conversation_history``.
It keeps the context epoch rules in one place:

- ``[summary] + [recent tail] + [current user]``;
- tool history is injected at its original user-turn anchor;
- only the last 2 user turns can carry tool history;
- system summaries are never dropped by the budget trim;
- the current user turn is excluded (the caller appends it).
"""

from __future__ import annotations

from typing import Any, Callable

from .tool_history import human_count, message_id, should_inject

DEFAULT_HISTORY_BUDGET_CHARS = 12_000
DEFAULT_SKIP_ROLES = ("tool",)

ContentText = Callable[[Any], str]


def _history_role(msg: Any, skip_roles: tuple[str, ...]) -> str | None:
    role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
    if role in ("human", "user"):
        return "user"
    if role in ("ai", "assistant"):
        return "assistant"
    if role in ("system",):
        return "system"
    if role in skip_roles:
        return None
    return None


def _msg_content(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _msg_id(msg: Any) -> str | None:
    return message_id(msg)


def assemble_model_view(
    state: dict[str, Any],
    *,
    content_text: ContentText,
    budget_chars: int | None = None,
    history_budget_chars: int = DEFAULT_HISTORY_BUDGET_CHARS,
    skip_roles: tuple[str, ...] = DEFAULT_SKIP_ROLES,
) -> list[dict[str, Any]]:
    raw = list(state.get("messages") or [])
    if not raw:
        return []

    # The final human message is this turn's request; the caller appends it.
    cut = len(raw)
    for i in range(len(raw) - 1, -1, -1):
        role = getattr(raw[i], "type", None) or getattr(raw[i], "role", None)
        if role in ("human", "user"):
            cut = i
            break

    tail_id = state.get("context_tail_start_id")
    start = 0
    if tail_id:
        for i, msg in enumerate(raw[:cut]):
            if _msg_id(msg) == tail_id:
                start = i
                break
        else:
            start = max(0, cut - 12)
    elif state.get("context_bound"):
        # Summary covers the whole old history; do not replay old raw messages.
        start = cut

    summary = str(state.get("context_summary") or "").strip()
    current_turn = human_count(raw)
    tool_history = state.get("tool_history") or []
    tool_turn = state.get("tool_history_turn")
    anchor_id = state.get("tool_history_anchor_id")
    inject_tools = bool(tool_history) and should_inject(tool_turn, current_turn)
    anchor_pos: int | None = None

    history: list[dict[str, Any]] = []
    if summary:
        history.append({"role": "system", "content": f"[历史摘要]\n{summary}"})

    for msg in raw[start:cut]:
        role = _history_role(msg, skip_roles)
        if role is None:
            continue
        text = content_text(_msg_content(msg))
        if summary and role == "system" and text.startswith("[历史摘要]"):
            continue
        if not text.strip():
            continue
        history.append({"role": role, "content": text})
        if inject_tools and anchor_id and _msg_id(msg) == anchor_id:
            anchor_pos = len(history)

    if inject_tools:
        injected = [dict(m) for m in tool_history if isinstance(m, dict)]
        if anchor_pos is not None:
            history[anchor_pos:anchor_pos] = injected
        else:
            history.extend(injected)

    if budget_chars is None:
        window = int(state.get("context_window") or 0)
        budget_chars = (
            max(8_000, min(60_000, int(window * 2)))
            if window > 0
            else history_budget_chars
        )

    systems = [h for h in history if h["role"] == "system"]
    convo = [h for h in history if h["role"] != "system"]
    total = sum(len(str(h.get("content") or "")) for h in convo)
    while convo and total > budget_chars:
        total -= len(str(convo[0].get("content") or ""))
        convo.pop(0)
    return systems + convo


__all__ = [
    "DEFAULT_HISTORY_BUDGET_CHARS",
    "DEFAULT_SKIP_ROLES",
    "assemble_model_view",
]
