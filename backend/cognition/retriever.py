"""混合检索（对齐 agent.md §4.1）。

- `HybridSearchRetriever`：**规范实现** —— 章节意图检测 → 向量+BM25 RRF 融合
  → 对话多召回 → 章节富化，子链路独立降级（任一失败不影响其余路径）。
  由 `app.rag.retriever`（兼容 shim）再导出为 `Retriever`，供 `app/runtime.py`、
  `graphmod/base.py`、`plugin_bridge.py` 使用旧导入路径。
- `HybridRetriever`：把任意基础检索器适配为认知层统一契约 `{content, meta, score}`。
- `BM25Retriever`：无外部依赖时的纯 BM25 兜底（优先 `rank_bm25`，缺失则
  退化为词频打分），保证检索链路在向量模型不可用时仍可用。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class HybridRetriever:
    """把既有检索器适配为认知层统一契约。

    `score_threshold` 默认 `0.35`（agent-2 §13.3.1 签名，与 `cognition/rag.py`
    的 `DEFAULT_THRESHOLD` 一致）。⚠ 该阈值作用在**归一化相似度**上（本适配器的 base
    返回 cos/rerank 分数，∈[0,1]）；RRF 融合分（`~1/60`）走的是另一条
    `HybridSearchRetriever` 路径，不受此默认值影响。
    """

    def __init__(
        self,
        base: Any | None = None,
        score_threshold: float = 0.35,
        fallback: "BM25Retriever | None" = None,
    ) -> None:
        self.base = base
        self.score_threshold = score_threshold
        self.fallback = fallback

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        raw: list[Any] = []
        if self.base is not None:
            try:
                if hasattr(self.base, "invoke"):
                    raw = self.base.invoke(query, top_k)
                elif callable(self.base):
                    raw = self.base(query, top_k)
            except Exception as exc:  # noqa: BLE001
                logger.warning("base retriever failed, falling back: %s", exc)
                raw = []

        if not raw and self.fallback is not None:
            return self.fallback.retrieve(query, top_k)

        hits = [self._normalize(item) for item in raw]
        hits = [h for h in hits if h["score"] >= self.score_threshold]
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    @staticmethod
    def _normalize(item: Any) -> dict[str, Any]:
        """把 (doc, score) / {text, metadata} / {content, meta} 归一化。"""
        doc: dict[str, Any]
        score: float
        if isinstance(item, tuple) and len(item) == 2:
            doc, score = item[0], float(item[1])
        elif isinstance(item, dict):
            doc = item
            score = float(item.get("score", 0.0) or 0.0)
        else:
            return {"content": str(item), "meta": {}, "score": 0.0}

        content = doc.get("content") or doc.get("text") or ""
        meta = doc.get("meta") or doc.get("metadata") or {}
        return {"content": content, "meta": meta, "score": score}

    def __call__(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self.retrieve(query, top_k)


class BM25Retriever:
    """轻量 BM25 检索器（rank_bm25 优先，缺失则纯词频）。"""

    def __init__(self, documents: list[str] | None = None, metadatas: list[dict] | None = None) -> None:
        self._docs: list[str] = []
        self._metas: list[dict] = []
        self._bm25: Any = None
        if documents:
            self.index(documents, metadatas)

    def index(self, documents: list[str], metadatas: list[dict] | None = None) -> None:
        self._docs = list(documents)
        self._metas = list(metadatas) if metadatas else [{} for _ in self._docs]
        self._bm25 = None
        try:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi([_tokenize(d) for d in self._docs])
        except Exception:  # noqa: BLE001
            logger.info("rank_bm25 unavailable, using frequency fallback")

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not self._docs:
            return []
        tokens = _tokenize(query)
        if self._bm25 is not None:
            scores = list(self._bm25.get_scores(tokens))
        else:
            scores = [float(sum(t in _tokenize(d) for t in tokens)) for d in self._docs]

        ranked = sorted(
            (
                {
                    "content": doc,
                    "meta": self._metas[i],
                    "score": round(float(scores[i]), 4),
                }
                for i, doc in enumerate(self._docs)
            ),
            key=lambda h: h["score"],
            reverse=True,
        )
        return ranked[:top_k]


# ── 规范多源检索算法（agent.md §4.1）──────────────────────────────────────
# 由 `app.rag.retriever` 迁入（算法逐字保留，仅将基础设施改为鸭子类型注入）。
# 旧导入路径 `app.rag.retriever.Retriever` 现再导出本类，故 RAGAgent 的
# retrieve seam 已由认知层接管，不改变其对外契约。
DIALOGUE_WEIGHT = 0.4


def _doc_key(doc: dict) -> str:
    """生成跨检索来源稳定的文档标识，用于 RRF 融合去重。

    不能用 id(doc)：向量检索与 BM25 返回的是不同的 dict 对象，
    同一文档会被当成两条，无法合并分数。
    """
    import hashlib

    meta = doc.get("metadata") or {}
    doc_id = meta.get("document_id") or ""
    text = doc.get("text") or ""
    return f"{doc_id}|{hashlib.md5(text.encode('utf-8', 'ignore')).hexdigest()}"


def _reciprocal_rank_fusion(
    vector_results: List[Tuple[dict, float]],
    bm25_results: List[Tuple[dict, float]],
    k: int = 60,
) -> List[Tuple[dict, float]]:
    """使用倒数排名融合（RRF）合并向量搜索和 BM25 搜索结果。

    agent-2 §13.3.1：`score(d) = Σ 1/(k + rank_i(d))`，k=60。**纯 RRF、不加两路权重**
    —— 排名靠前的结果自然获得更高融合分，无需调权重（D12 明确选择纯 RRF 而非"加权融合"）。
    """
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, (doc, _) in enumerate(vector_results):
        key = _doc_key(doc)
        if key not in docs:
            docs[key] = doc
        scores[key] = 1.0 / (rank + k)

    for rank, (doc, _) in enumerate(bm25_results):
        key = _doc_key(doc)
        if key not in docs:
            docs[key] = doc
            scores[key] = 0
        scores[key] += 1.0 / (rank + k)

    ranked = sorted(
        [(docs[k2], v) for k2, v in scores.items() if v > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


def _dialogue_rrf(
    main_results: List[Tuple[dict, float]],
    dialogue_results: List[Tuple[dict, float]],
    k: int = 60,
) -> List[Tuple[dict, float]]:
    """将主搜索结果与对话多召回结果进行 RRF 融合。"""
    if not dialogue_results:
        return main_results
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, (doc, _) in enumerate(main_results):
        key = _doc_key(doc)
        if key not in docs:
            docs[key] = doc
        scores[key] = 1.0 * (1.0 / (rank + k))

    for rank, (doc, _) in enumerate(dialogue_results):
        key = _doc_key(doc)
        if key not in docs:
            docs[key] = doc
            scores[key] = 0
        scores[key] += DIALOGUE_WEIGHT * (1.0 / (rank + k))

    ranked = sorted(
        [(docs[k2], v) for k2, v in scores.items() if v > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


class HybridSearchRetriever:
    """混合检索器：整合向量搜索、BM25、对话召回和章节检索。

    基础设施（`vector_store` / `embeddings` / `bm25_index` / `chapter_store`）为
    鸭子类型依赖，只要求提供原 `app.rag.Retriever` 使用到的那几个方法。
    """

    def __init__(
        self,
        vector_store: Any,
        embeddings: Any,
        bm25_index: Optional[Any] = None,
        chapter_store: Optional[Any] = None,
    ) -> None:
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.bm25_index = bm25_index
        self.chapter_store = chapter_store

    def invoke(self, query: str, k: int = 5) -> List[Tuple[dict, float]]:
        """执行混合检索：章节意图检测 → 向量+BM25 融合 → 对话多召回 → 章节信息丰富。

        子链路独立降级：向量/对话/章节任一失败不影响其余路径，最坏返回 BM25 或空结果，
        而不是整体抛错打断问答（对齐"检索链路异常隔离"）。
        """
        from cognition.intent import detect_chapter_intent

        # Step 1: detect chapter intent
        intent = detect_chapter_intent(query)
        if intent and self.chapter_store:
            try:
                chapter_results = self._chapter_lookup(intent)
            except Exception as e:  # noqa: BLE001
                logger.warning("chapter lookup failed, continue hybrid search: %s", e)
                chapter_results = None
            if chapter_results:
                return chapter_results[:k]

        # Step 2: normal hybrid search（向量子链路失败降级为纯 BM25）
        query_embedding = None
        vector_results: List[Tuple[dict, float]] = []
        try:
            query_embedding = self.embeddings.embed_query(query)
            vector_results = self.vector_store.similarity_search(query_embedding, k=k * 2)
        except Exception as e:  # noqa: BLE001
            logger.warning("vector search failed, falling back to BM25-only: %s", e)

        if self.bm25_index:
            try:
                bm25_results = self.bm25_index.search(query, k=k * 2)
            except Exception as e:  # noqa: BLE001
                logger.warning("bm25 search failed: %s", e)
                bm25_results = []
            if vector_results:
                fused = _reciprocal_rank_fusion(vector_results, bm25_results)
                results = fused[:k]
            else:
                results = bm25_results[:k]
        else:
            results = vector_results[:k]

        # Step 3: dialogue multi-recall（失败不影响主结果）
        if query_embedding is not None:
            try:
                dialogue_results = self._dialogue_search(query_embedding, k=3)
            except Exception as e:  # noqa: BLE001
                logger.warning("dialogue search failed: %s", e)
                dialogue_results = []
            if dialogue_results:
                results = _dialogue_rrf(results, dialogue_results)[:k]

        # Enrich with parent chapter info（失败不影响已检索结果）
        if self.chapter_store:
            try:
                results = self._enrich_with_parent(results)
            except Exception as e:  # noqa: BLE001
                logger.warning("chapter enrichment failed: %s", e)

        return results

    def _dialogue_search(self, query_embedding: List[float], k: int = 3) -> List[Tuple[dict, float]]:
        """检索对话类型的文档块，用于多召回融合。"""
        return self.vector_store.similarity_search(
            query_embedding, k=k * 2, where={"is_dialogue": True}
        )

    def _chapter_lookup(self, intent: dict) -> List[Tuple[dict, float]]:
        """Direct chapter metadata lookup when intent is detected."""
        chap_title = intent.get("chapter_title_raw", "")
        chap_number = intent.get("chapter_number")

        chapters = []
        if chap_number is not None:
            chapters = self.chapter_store.find_by_number(None, chap_number)
        if not chapters and chap_title:
            keyword = (
                chap_title.replace("第", "").replace("章", "").replace("节", "")
                .replace("Chapter", "").replace("chapter", "").strip()
            )
            if keyword:
                chapters = self.chapter_store.find_by_keyword(keyword)

        results = []
        for ch in chapters:
            results.append((
                {
                    "text": ch["summary"],
                    "metadata": {
                        "document_id": ch["document_id"],
                        "filename": ch["document_filename"],
                        "chapter_title": ch["chapter_title"],
                        "chapter_number": ch["chapter_number"],
                        "source": ch["document_filename"],
                        "chapter_summary": ch["summary"],
                        "_chapter_lookup": True,
                    },
                },
                1.0,
            ))

        if not results:
            return []

        # Also fetch child chunks for this chapter from vector store
        child_docs, _ = self.vector_store.get_chunks(
            document_id=results[0][0]["metadata"]["document_id"],
            query=results[0][0]["metadata"]["chapter_title"],
        )
        for cd in child_docs[:5]:
            results.append((
                {
                    "text": cd["text"],
                    "metadata": {
                        **cd["metadata"],
                        "_chapter_lookup": True,
                    },
                },
                0.95,
            ))

        return results

    def _enrich_with_parent(self, results: List[Tuple[dict, float]]) -> List[Tuple[dict, float]]:
        """为检索结果补充父章节摘要信息。"""
        chapter_titles = list({
            doc["metadata"]["chapter_title"]
            for doc, _ in results
            if doc["metadata"].get("chapter_title")
        })
        if chapter_titles:
            chapters = self.chapter_store.find_by_keywords(chapter_titles)
            lookup = {ch["chapter_title"]: ch for ch in chapters}
            for doc, _ in results:
                ct = doc["metadata"].get("chapter_title")
                if ct and ct in lookup:
                    doc["metadata"]["chapter_summary"] = lookup[ct]["summary"]
        return results

    @property
    def is_empty(self) -> bool:
        """判断向量库是否为空。"""
        return self.vector_store.count == 0


# 兼容别名：旧 `app.rag.retriever.Retriever` 的规范实现现为 `HybridSearchRetriever`。
Retriever = HybridSearchRetriever
