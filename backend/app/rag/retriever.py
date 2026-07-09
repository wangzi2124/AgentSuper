from typing import List, Tuple, Optional, TYPE_CHECKING

from app.rag.vector_store import VectorStore
from app.rag.embeddings import LocalEmbeddings
from app.rag.bm25_index import BM25Index
from app.rag.intent import detect_chapter_intent

if TYPE_CHECKING:
    from app.rag.chapter_store import ChapterStore

VECTOR_WEIGHT = 0.7
BM25_WEIGHT = 0.3
DIALOGUE_WEIGHT = 0.4


def _reciprocal_rank_fusion(
    vector_results: List[Tuple[dict, float]],
    bm25_results: List[Tuple[dict, float]],
    k: int = 60,
) -> List[Tuple[dict, float]]:
    scores: dict[int, float] = {}
    docs: list[dict] = []

    for rank, (doc, _) in enumerate(vector_results):
        idx = id(doc)
        docs.append(doc)
        scores[idx] = VECTOR_WEIGHT * (1.0 / (rank + k))

    for rank, (doc, _) in enumerate(bm25_results):
        idx = id(doc)
        if idx not in scores:
            docs.append(doc)
            scores[idx] = 0
        scores[idx] += BM25_WEIGHT * (1.0 / (rank + k))

    ranked = sorted(
        [(d, scores.get(id(d), 0)) for d in docs if scores.get(id(d), 0) > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


def _dialogue_rrf(
    main_results: List[Tuple[dict, float]],
    dialogue_results: List[Tuple[dict, float]],
    k: int = 60,
) -> List[Tuple[dict, float]]:
    if not dialogue_results:
        return main_results
    scores: dict[int, float] = {}
    docs: list[dict] = []

    for rank, (doc, _) in enumerate(main_results):
        idx = id(doc)
        docs.append(doc)
        scores[idx] = 1.0 * (1.0 / (rank + k))

    for rank, (doc, _) in enumerate(dialogue_results):
        idx = id(doc)
        if idx not in scores:
            docs.append(doc)
            scores[idx] = 0
        scores[idx] += DIALOGUE_WEIGHT * (1.0 / (rank + k))

    ranked = sorted(
        [(d, scores.get(id(d), 0)) for d in docs if scores.get(id(d), 0) > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


class Retriever:
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
        # Step 1: detect chapter intent
        intent = detect_chapter_intent(query)
        if intent and self.chapter_store:
            chapter_results = self._chapter_lookup(intent)
            if chapter_results:
                return chapter_results[:k]

        # Step 2: normal hybrid search
        query_embedding = self.embeddings.embed_query(query)
        vector_results = self.vector_store.similarity_search(query_embedding, k=k * 2)

        if self.bm25_index:
            bm25_results = self.bm25_index.search(query, k=k * 2)
            fused = _reciprocal_rank_fusion(vector_results, bm25_results)
            results = fused[:k]
        else:
            results = vector_results[:k]

        # Step 3: dialogue multi-recall
        dialogue_results = self._dialogue_search(query_embedding, k=3)
        if dialogue_results:
            results = _dialogue_rrf(results, dialogue_results)[:k]

        # Enrich with parent chapter info
        if self.chapter_store:
            results = self._enrich_with_parent(results)

        return results

    def _dialogue_search(self, query_embedding: List[float], k: int = 3) -> List[Tuple[dict, float]]:
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
        return self.vector_store.count == 0
