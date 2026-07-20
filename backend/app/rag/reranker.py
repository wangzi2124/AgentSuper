import logging
from pathlib import Path
from typing import List, Optional, Tuple

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)


class Reranker:
    """基于 CrossEncoder 模型的重排序器，对检索结果进行精排。"""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        cache_dir: Optional[Path] = None,
    ):
        self.model_name = model_name
        self.backend_dir = Path(__file__).resolve().parents[2]
        self.cache_dir = cache_dir or (self.backend_dir / "data" / "models")
        self.model = self._load_model(model_name)

    def _resolve_local_model(self, model_name: str) -> Optional[Path]:
        """查找本地缓存的重排序模型路径。"""
        candidates = [
            Path(model_name),
            self.backend_dir / model_name,
            self.cache_dir / model_name,
            self.cache_dir / model_name.replace("/", "_").replace(":", "_"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        if self.cache_dir.exists():
            for sub in self.cache_dir.rglob(model_name):
                if sub.is_dir():
                    return sub.resolve()
        return None

    def _load_model(self, model_name: str) -> CrossEncoder:
        """加载 CrossEncoder 模型，优先使用本地缓存。"""
        local_model = self._resolve_local_model(model_name)
        if local_model:
            logger.info("Loading reranker from local path: %s", local_model)
            return CrossEncoder(str(local_model))

        logger.info("Loading reranker from HuggingFace: %s", model_name)
        return CrossEncoder(model_name)

    def rerank(
        self,
        query: str,
        documents: List[dict],
        top_k: int = 3,
    ) -> List[Tuple[dict, float]]:
        """对文档列表按 query 相关性重新排序，返回 top_k 结果。"""
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
