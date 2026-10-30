"""RAG 管线（对齐 agent.md §4.1）。

分块 → 向量化 → 入库 → 检索（带相似度阈值）→ 重排 → 格式化（带来源标注）。

向量库默认使用 ChromaDB 持久化客户端；`embed_fn` 可注入以便测试或替换模型。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from storage.paths import data_path

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "knowledge_base"
DEFAULT_THRESHOLD = 0.35
DEFAULT_TOP_K = 5
BATCH_SIZE = 5000

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PERSIST_DIR = data_path(None, "rag")


def _default_embed(texts: list[str]) -> list[list[float]]:
    from models.router import embed

    return embed(texts)


def _doc_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


class RAGPipeline:
    """最小 RAG 管线：索引入库 + 带阈值的检索 + 来源标注。"""

    def __init__(
        self,
        collection_name: str = DEFAULT_COLLECTION,
        persist_dir: str | Path | None = None,
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
        score_threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        import chromadb

        self.collection_name = collection_name
        self.score_threshold = score_threshold
        self.embed_fn = embed_fn or _default_embed
        self.persist_dir = str(persist_dir or DEFAULT_PERSIST_DIR)
        self._client = chromadb.PersistentClient(path=self.persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # ---- 入库 ----
    def index(
        self,
        documents: Sequence[str],
        metadatas: Sequence[dict[str, Any]] | None = None,
        ids: Sequence[str] | None = None,
    ) -> int:
        """向量化并写入向量库，返回写入条数。"""
        docs = list(documents)
        if not docs:
            return 0
        metas = list(metadatas) if metadatas else [{"source": "unknown"} for _ in docs]
        doc_ids = list(ids) if ids else [f"doc-{_doc_id(d)}" for d in docs]

        written = 0
        for start in range(0, len(docs), BATCH_SIZE):
            chunk = docs[start : start + BATCH_SIZE]
            vectors = self.embed_fn(chunk)
            self._collection.add(
                ids=doc_ids[start : start + BATCH_SIZE],
                documents=chunk,
                embeddings=vectors,
                metadatas=metas[start : start + BATCH_SIZE],
            )
            written += len(chunk)
        return written

    # ---- 检索 ----
    def retrieve(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """相似度检索，过滤低于阈值的片段，返回 {content, meta, score}。"""
        if not query.strip():
            return []
        threshold = self.score_threshold if score_threshold is None else score_threshold
        vector = self.embed_fn([query])[0]
        resp = self._collection.query(
            query_embeddings=[vector],
            n_results=max(top_k * 2, top_k),
            include=["documents", "metadatas", "distances"],
        )
        docs = (resp.get("documents") or [[]])[0]
        metas = (resp.get("metadatas") or [[]])[0]
        dists = (resp.get("distances") or [[]])[0]

        hits: list[dict[str, Any]] = []
        for text, meta, dist in zip(docs, metas, dists):
            score = 1.0 - float(dist)  # cosine distance → 相似度
            if score < threshold:
                continue
            hits.append({"content": text, "meta": meta or {}, "score": round(score, 4)})
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top_k]

    # ---- 格式化 ----
    @staticmethod
    def format_context(chunks: list[dict[str, Any]]) -> str:
        """拼接检索结果为带来源标注的上下文块（对齐 §4.1.4）。"""
        if not chunks:
            return ""
        blocks: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            meta = chunk.get("meta") or {}
            source = meta.get("source") or meta.get("filename") or "未知来源"
            page = meta.get("page")
            locator = f"{source}#p{page}" if page not in (None, "") else source
            blocks.append(f"[{i}] {chunk['content']}\n来源: {locator}")
        return "\n\n---\n\n".join(blocks)

    def as_retriever(self) -> Callable[[str, int], list[dict[str, Any]]]:
        """返回形如 `(query, top_k) -> list[dict]` 的可调用对象，供 graph 节点注入。"""

        def _retrieve(query: str, top_k: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
            return self.retrieve(query, top_k=top_k)

        return _retrieve

    @property
    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        """清空集合（测试用）。"""
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )


def rag_search(
    query: str,
    pipeline: RAGPipeline,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """便捷函数：检索 + 格式化，返回 {context, sources}。"""
    hits = pipeline.retrieve(query, top_k=top_k)
    sources = sorted(
        {
            str((h.get("meta") or {}).get("source") or (h.get("meta") or {}).get("filename") or "")
            for h in hits
        }
        - {""}
    )
    return {"context": pipeline.format_context(hits), "sources": sources, "hits": hits}
