from pathlib import Path
from typing import List, Optional, Tuple

from sentence_transformers import CrossEncoder


class Reranker:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        cache_dir: Optional[Path] = None,
    ):
        self.model_name = model_name
        self.backend_dir = Path(__file__).resolve().parents[2]
        self.cache_dir = cache_dir or (self.backend_dir / "data" / "models")
        self.model = self._load_model(model_name)

    def _load_model(self, model_name: str) -> CrossEncoder:
        return CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        documents: List[dict],
        top_k: int = 3,
    ) -> List[Tuple[dict, float]]:
        if not documents:
            return []

        pairs = [[query, doc["content"]] for doc in documents]
        scores = self.model.predict(pairs)

        ranked = sorted(
            zip(documents, scores),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_k]
