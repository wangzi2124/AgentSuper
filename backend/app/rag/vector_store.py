import uuid

import chromadb
from chromadb.config import Settings as ChromaSettings
from typing import List, Tuple, Optional

CHROMA_MAX_BATCH_SIZE = 5000


class VectorStore:
    """基于 ChromaDB 的向量存储，支持文档增删查及相似度搜索。"""

    def __init__(self, persist_dir: str):
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self, texts: List[str], metadatas: List[dict], embeddings: List[List[float]]
    ) -> List[str]:
        """批量添加文档到向量库，返回生成的 ID 列表。"""
        ids = [str(uuid.uuid4()) for _ in texts]
        for i in range(0, len(texts), CHROMA_MAX_BATCH_SIZE):
            end = i + CHROMA_MAX_BATCH_SIZE
            self.collection.add(
                ids=ids[i:end],
                documents=texts[i:end],
                metadatas=metadatas[i:end],
                embeddings=embeddings[i:end],
            )
        return ids

    def similarity_search(
        self, query_embedding: List[float], k: int = 4, where: Optional[dict] = None
    ) -> List[Tuple[dict, float]]:
        """基于向量相似度搜索，返回 (文档条目, 相似度分数) 列表。"""
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        if not results["ids"][0]:
            return []

        entries = []
        for i in range(len(results["ids"][0])):
            entry = {
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
            }
            score = 1 - results["distances"][0][i]
            entries.append((entry, score))
        return entries

    def delete_by_metadata(self, key: str, value: str):
        """按元数据字段值删除匹配的文档。"""
        existing = self.collection.get(where={key: value})
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])

    def get_all(self) -> Tuple[List[str], List[dict]]:
        """获取向量库中所有文档文本和元数据。"""
        results = self.collection.get(include=["documents", "metadatas"])
        return results.get("documents", []) or [], results.get("metadatas", []) or []

    def get_chunks(
        self, offset: int = 0, limit: int = 50,
        document_id: Optional[str] = None, query: Optional[str] = None
    ) -> Tuple[List[dict], int]:
        """分页获取文档块，支持按文档 ID 和关键词过滤。"""
        where = {"document_id": document_id} if document_id else None
        where_document = {"$contains": query} if query else None

        if where or where_document:
            all_results = self.collection.get(
                where=where,
                where_document=where_document,
                include=["documents", "metadatas"],
            )
            total = len(all_results["ids"])
            # Use slice indices directly to preserve ChromaDB's ordering
            page_ids = all_results["ids"][offset:offset + limit]
            page_ids_set = set(page_ids)
            chunks = []
            for i, id_ in enumerate(all_results["ids"]):
                if id_ not in page_ids_set:
                    continue
                # Only include entries that are in the current page, preserving order
                chunks.append({
                    "id": id_,
                    "text": all_results["documents"][i] if all_results["documents"] else "",
                    "metadata": all_results["metadatas"][i] if all_results["metadatas"] else {},
                })
            # Reorder chunks to match page_ids order (in case of duplicate IDs)
            id_order = {id_: idx for idx, id_ in enumerate(page_ids)}
            chunks.sort(key=lambda c: id_order.get(c["id"], float("inf")))
        else:
            results = self.collection.get(
                offset=offset,
                limit=limit,
                include=["documents", "metadatas"],
            )
            total = self.collection.count()
            chunks = []
            for i in range(len(results["ids"])):
                chunks.append({
                    "id": results["ids"][i],
                    "text": results["documents"][i] if results["documents"] else "",
                    "metadata": results["metadatas"][i] if results["metadatas"] else {},
                })
        return chunks, total

    @classmethod
    def load(cls, persist_dir: str) -> "VectorStore":
        """从持久化目录加载已有的向量存储实例。"""
        return cls(persist_dir)

    @property
    def count(self) -> int:
        """返回向量库中文档总数。"""
        return self.collection.count()
