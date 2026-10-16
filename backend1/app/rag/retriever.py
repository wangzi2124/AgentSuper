import logging
from typing import List, Tuple, Optional, TYPE_CHECKING

from app.rag.vector_store import VectorStore
from app.rag.embeddings import LocalEmbeddings
from app.rag.bm25_index import BM25Index
from app.rag.intent import detect_chapter_intent

if TYPE_CHECKING:
    from app.rag.chapter_store import ChapterStore

logger = logging.getLogger(__name__)

VECTOR_WEIGHT = 0.7
BM25_WEIGHT = 0.3
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
    """使用倒数排名融合（RRF）合并向量搜索和 BM25 搜索结果。"""
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, (doc, _) in enumerate(vector_results):
        key = _doc_key(doc)
        if key not in docs:
            docs[key] = doc
        scores[key] = VECTOR_WEIGHT * (1.0 / (rank + k))

    for rank, (doc, _) in enumerate(bm25_results):
        key = _doc_key(doc)
        if key not in docs:
            docs[key] = doc
            scores[key] = 0
        scores[key] += BM25_WEIGHT * (1.0 / (rank + k))

    ranked = sorted(
        [(docs[k], v) for k, v in scores.items() if v > 0],
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
        [(docs[k], v) for k, v in scores.items() if v > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


class Retriever:
    """混合检索器，整合向量搜索、BM25、对话召回和章节检索。"""

    def __init__(
        self,
        vector_store: VectorStore,
        embeddings: LocalEmbeddings,
        bm25_index: Optional[BM25Index] = None,
        chapter_store: Optional["ChapterStore"] = None,
    ):
        self.vector_store = vector_store
        self.embeddings = embeddings
        self.bm25_index = bm25_index
        self.chapter_store = chapter_store

    def invoke(self, query: str, k: int = 5) -> List[Tuple[dict, float]]:
        """执行混合检索：章节意图检测 → 向量+BM25 融合 → 对话多召回 → 章节信息丰富。

        子链路独立降级：向量/对话/章节任一失败不影响其余路径，最坏返回 BM25 或空结果，
        而不是整体抛错打断问答（对齐"检索链路异常隔离"）。
        """
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
            keyword = chap_title.replace("第", "").replace("章", "").replace("节", "").replace("Chapter", "").replace("chapter", "").strip()
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
