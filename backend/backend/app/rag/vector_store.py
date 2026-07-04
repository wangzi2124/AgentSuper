import uuid

import chromadb
from chromadb.config import Settings as ChromaSettings
from typing import List, Tuple, Optional

CHROMA_MAX_BATCH_SIZE = 5000


class VectorStore:
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
        existing = self.collection.get(where={key: value})
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])

    def get_all(self) -> Tuple[List[str], List[dict]]:
        results = self.collection.get(include=["documents", "metadatas"])
        return results.get("documents", []) or [], results.get("metadatas", []) or []

    def get_chunks(
        self, offset: int = 0, limit: int = 50,
        document_id: Optional[str] = None, query: Optional[str] = None
    ) -> Tuple[List[dict], int]:
        where = {"document_id": document_id} if document_id else None
        where_document = {"$contains": query} if query else None
        results = self.collection.get(
            where=where,
            where_document=where_document,
            offset=offset,
            limit=limit,
            include=["documents", "metadatas"],
        )
        if where or where_document:
            total = len(self.collection.get(
                where=where, where_document=where_document, include=[]
            )["ids"])
        else:
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
        return cls(persist_dir)

    @property
    def count(self) -> int:
        return self.collection.count()
