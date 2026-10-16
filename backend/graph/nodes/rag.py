"""RAG 检索节点（对齐 agent.md §4.1）。

M1 保持解耦：接受任意 `retrieve(query, top_k) -> list[dict]` 回调；
真正的混合检索管线在 M2 的 `cognition/` 中实现。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _hit_text(hit: Any) -> str:
    if isinstance(hit, dict):
        return str(hit.get("content") or hit.get("text") or "")
    return str(hit)


def _hit_source(hit: Any) -> str:
    if not isinstance(hit, dict):
        return ""
    meta = hit.get("meta") or hit.get("metadata") or {}
    return str(meta.get("source") or meta.get("filename") or "")


def make_rag_node(
    retrieve: Callable[..., list[dict[str, Any]]] | None = None,
    top_k: int = DEFAULT_TOP_K,
    reranker: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def rag_node(state: dict[str, Any]) -> dict[str, Any]:
        query = _last_user_text(state)
        if retrieve is None or not query:
            return {
                "rag_context": "",
                "messages": [SystemMessage("[RAG] 未配置检索器或无查询。")],
            }
        try:
            hits = retrieve(query, top_k)
        except TypeError:
            hits = retrieve(query)
        except Exception as e:  # noqa: BLE001
            return {
                "rag_context": "",
                "messages": [SystemMessage(f"[RAG] 检索失败：{e}")],
            }
        if not hits:
            return {
                "rag_context": "",
                "messages": [SystemMessage("未检索到相关内容。")],
            }
        if reranker is not None:
            try:
                hits = reranker.rerank(query, hits, top_k=top_k)
            except Exception as e:  # noqa: BLE001
                logger.warning("rerank skipped: %s", e)
        lines = []
        for i, h in enumerate(hits):
            score = h.get("score") if isinstance(h, dict) else None
            prefix = f"[来源 {i + 1}]"
            if score is not None:
                prefix += f" (相关度 {float(score):.2f})"
            source = _hit_source(h)
            if source:
                prefix += f" {source}"
            lines.append(f"{prefix}\n{_hit_text(h)}")
        context = "\n\n".join(lines)
        return {
            "rag_context": context,
            "messages": [SystemMessage(f"[RAG 检索结果]\n{context}")],
        }

    return rag_node
