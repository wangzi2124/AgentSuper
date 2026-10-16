"""记忆节点（对齐 agent.md §4.2）。

长/短期记忆通过 LangGraph Store 组织（namespace = (user_id, "memories")）。
`store` 由 build_main_graph 注入；未注入时节点为 no-op。
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable

MEMORY_NAMESPACE_SUFFIX = "memories"


def _namespace(user_id: str) -> tuple[str, str]:
    return (user_id or "default", MEMORY_NAMESPACE_SUFFIX)


def _key(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def make_memory_recall_node(
    store: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def memory_recall_node(state: dict[str, Any]) -> dict[str, Any]:
        if store is None:
            return {"memory_context": ""}
        user_id = state.get("user_id", "default")
        query = _last_user_text(state)
        try:
            memories = store.search(_namespace(user_id), query=query, limit=5)
        except Exception:  # noqa: BLE001
            memories = []
        ctx = "\n".join(
            f"- [{getattr(m, 'value', {}).get('type', '?')}] "
            f"{getattr(m, 'value', {}).get('content', '')}"
            for m in memories
        )
        return {"memory_context": ctx}

    return memory_recall_node


def make_memory_store_node(
    store: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def memory_store_node(state: dict[str, Any]) -> dict[str, Any]:
        if store is None:
            return {}
        user_id = state.get("user_id", "default")
        ns = _namespace(user_id)
        if summary := state.get("context_summary"):
            store.put(
                ns,
                key=_key("summary", summary),
                value={"content": summary, "type": "conversation_summary"},
            )
        if final := state.get("final_output"):
            store.put(
                ns,
                key=_key("result", final),
                value={"content": final, "type": "task_result"},
            )
        return {}

    return memory_store_node
