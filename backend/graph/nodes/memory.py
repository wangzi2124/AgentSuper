"""记忆节点（对齐 agent.md §4.2）。

长/短期记忆通过 LangGraph Store 组织（namespace = (user_id, "memories")）。
`store` 由 build_main_graph 注入；未注入时节点为 no-op。
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable

from .base import content_text

MEMORY_NAMESPACE_SUFFIX = "memories"


def _namespace(user_id: str) -> tuple[str, str]:
    return (user_id or "default", MEMORY_NAMESPACE_SUFFIX)


def _key(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            # 多模态 content 是分块数组（image_url 块含 base64）——必须走 content_text，
            # `str(content)` 会把整个 base64 灌进检索查询（base.py:123-128）。
            return content_text(getattr(msg, "content", ""))
    return ""


def _overlap_score(query: str, text: str) -> float:
    """字符级重合度：Store 没有向量索引时也能按 query 做轻量相关度排序。"""
    q = {ch.lower() for ch in query if not ch.isspace()}
    if not q:
        return 0.0
    t = {ch.lower() for ch in text if not ch.isspace()}
    return len(q & t) / len(q)


def _rank_memories(memories: list[Any], query: str, limit: int) -> list[Any]:
    if not query:
        return memories[:limit]
    scored = [
        (_overlap_score(query, str(getattr(m, "value", {}).get("content", ""))), i, m)
        for i, m in enumerate(memories)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [m for _, _, m in scored[:limit]]


def make_memory_recall_node(
    store: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def memory_recall_node(state: dict[str, Any]) -> dict[str, Any]:
        if store is None:
            return {"memory_context": ""}
        user_id = state.get("user_id", "default")
        query = _last_user_text(state)
        try:
            # 多取一些再由 _rank_memories 重排；无向量索引时 query 不会丢。
            memories = store.search(_namespace(user_id), query=query, limit=50)
        except Exception:  # noqa: BLE001
            memories = []
        memories = _rank_memories(memories, query, 5)
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
