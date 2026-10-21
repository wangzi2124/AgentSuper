"""RAG 检索节点（对齐 agent.md §4.1 / agent-2.md §13.4）。

M1 保持解耦：接受任意 `retrieve(query, top_k) -> list[dict]` 回调；
真正的混合检索管线在 M2 的 `cognition/` 中实现。

§13.4：检索后加 `grade_docs` 质量门禁节点 + `rewrite` 查询改写节点。
两者均用 `tier-simple` 模型（复用 §1.3 的 compress-model），
不相关则改写重试（`rewrite_count` 最多 2 次）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from langchain_core.messages import SystemMessage

from .base import content_text

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
# §13.4.2：改写重试上限（超过则带结果继续，避免检索死循环）
MAX_REWRITE = 2
# §13.4.2：grade 只看上下文前 2000 字符（成本约束）
GRADE_CONTEXT_CHARS = 2000


def _last_user_text(state: dict[str, Any]) -> str:
    for msg in reversed(state.get("messages") or []):
        if getattr(msg, "type", None) in ("human", "user"):
            # 多模态 content 是分块数组（image_url 块含 base64）——必须走 content_text，
            # `str(content)` 会把整个 base64 灌进检索查询（base.py:123-128）。
            return content_text(getattr(msg, "content", ""))
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


def _retrieve_query(state: dict[str, Any]) -> str:
    """§13.4.2：优先使用改写后的查询（`rewritten_query`），否则用原始用户消息。"""
    rewritten = str(state.get("rewritten_query") or "").strip()
    return rewritten or _last_user_text(state)


def make_rag_node(
    retrieve: Callable[..., list[dict[str, Any]]] | None = None,
    top_k: int = DEFAULT_TOP_K,
    reranker: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def rag_node(state: dict[str, Any]) -> dict[str, Any]:
        query = _retrieve_query(state)
        if retrieve is None or not query:
            return {
                "rag_context": "",
                "rag_done": True,
                "messages": [SystemMessage("[RAG] 未配置检索器或无查询。")],
            }
        try:
            hits = retrieve(query, top_k)
        except TypeError:
            hits = retrieve(query)
        except Exception as e:  # noqa: BLE001
            return {
                "rag_context": "",
                "rag_done": True,
                "messages": [SystemMessage(f"[RAG] 检索失败：{e}")],
            }
        if not hits:
            return {
                "rag_context": "",
                "rag_done": True,
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
            text = _hit_text(h)
            # §13.6 父块展开：子块精确命中 + 父小节提供完整上下文
            parent = ""
            if isinstance(h, dict):
                parent = str((h.get("meta") or {}).get("parent_content") or "")
            if parent:
                text = f"{text}\n[父块上下文] {parent}"
            lines.append(f"{prefix}\n{text}")
        context = "\n\n".join(lines)
        return {
            "rag_context": context,
            "rag_done": True,
            "messages": [SystemMessage(f"[RAG 检索结果]\n{context}")],
        }

    return rag_node


# ── §13.4 检索质量门禁（grade_docs）+ 查询改写（rewrite）────────────────────

GRADE_PROMPT = (
    "判断以下文档是否与用户问题相关。只输出 yes 或 no。\n\n"
    "文档：{context}\n"
    "问题：{question}"
)

REWRITE_FALLBACK_PROMPT = (
    "将用户问题改写为更利于检索的自包含查询，只输出改写后的查询文本，不要解释。\n"
    "用户问题：{question}"
)


def _model_text(resp: Any) -> str:
    """从模型响应中取出纯文本（兼容 str / {content} / LangChain AIMessage）。"""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return str(resp.get("content") or "")
    return str(getattr(resp, "content", "") or "")


def _invoke(model: Any, prompt: str) -> str:
    """调用 simple tier 模型，兼容 `.invoke` / 可调用对象两种契约。"""
    if hasattr(model, "invoke"):
        return _model_text(model.invoke(prompt))
    if callable(model):
        return _model_text(model(prompt))
    return ""


def make_grade_node(
    grade_model: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """§13.4.2 grade_docs：用 tier-simple 判断检索结果是否与问题相关。

    - 检索为空（`rag_context` 为空）→ 视为不相关，但**不触发改写重试**
      （无结果时改写无意义），直接 `rag_relevant=True` 放行到 generate。
    - 模型不可用/失败 → 保守放行（`rag_relevant=True`），不阻塞主流程。
    """

    def grade_documents_node(state: dict[str, Any]) -> dict[str, Any]:
        context = str(state.get("rag_context") or "")
        if not context.strip():
            # 空结果：放行（改写也检不到），避免 2 轮无效 grade/rewrite
            return {"rag_relevant": True}
        if grade_model is None:
            return {"rag_relevant": True}
        question = _last_user_text(state)
        prompt = GRADE_PROMPT.format(
            context=context[:GRADE_CONTEXT_CHARS], question=question
        )
        try:
            verdict = _invoke(grade_model, prompt).strip().lower()
        except Exception as e:  # noqa: BLE001
            logger.warning("grade failed, pass through: %s", e)
            return {"rag_relevant": True}
        return {"rag_relevant": "yes" in verdict}

    return grade_documents_node


def make_rewrite_node(
    rewrite_model: Any | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """§13.4.2 rewrite：改写原始查询为自包含检索查询，写回 `rewritten_query`。

    优先复用 `prompts/nodes/rag_rewrite.md` 模板（含 JSON 输出格式），
    解析 `rewritten_query` 字段；无模板/模型时用内联兜底提示。
    """

    def _rewrite_prompt(question: str) -> str:
        try:
            from prompts.loader import PromptLoader

            # PromptLoader.load 自身会把 {{var}} 归一为 $var 并 safe_substitute，
            # 必须**通过 kwargs 传变量**；再手工 replace 是无效的（此时已是 $ 形式）。
            return PromptLoader().load(
                "nodes/rag_rewrite.md",
                conversation_history="(无可用的历史上下文)",
                user_query=question,
            )
        except Exception:  # noqa: BLE001
            return REWRITE_FALLBACK_PROMPT.format(question=question)

    def _parse_rewritten(text: str, fallback: str) -> str:
        """从 JSON 输出里取 rewritten_query；无有效值则回退原始问题。"""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
                rq = str(data.get("rewritten_query") or "").strip()
                # 模型判为非检索输入（is_retrieval=false）或未给出改写 → 保留原问题
                return rq or fallback
            except Exception:  # noqa: BLE001
                pass
        cleaned = text.strip()
        # 原样返回的文本若仍是 JSON 片段（解析失败），不可当作查询
        if not cleaned or cleaned.startswith("{"):
            return fallback
        return cleaned

    def rewrite_query_node(state: dict[str, Any]) -> dict[str, Any]:
        # 条件路由函数不能写状态 → `rewrite_count` 在此自增（§13.4.2 单轮生命周期）。
        next_count = int(state.get("rewrite_count", 0) or 0) + 1
        question = _last_user_text(state)
        if rewrite_model is None or not question:
            return {"rewritten_query": question, "rewrite_count": next_count}
        try:
            out = _invoke(rewrite_model, _rewrite_prompt(question))
        except Exception as e:  # noqa: BLE001
            logger.warning("rewrite failed, keep original: %s", e)
            out = ""
        return {
            "rewritten_query": _parse_rewritten(out, question),
            "rewrite_count": next_count,
        }

    return rewrite_query_node

