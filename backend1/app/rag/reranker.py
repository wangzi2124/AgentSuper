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

    MODEL_TO_MODELSCOPE_ID = {
        "cross-encoder/ms-marco-MiniLM-L-6-v2": "cross-encoder/ms-marco-MiniLM-L6-v2",
    }

    def _resolve_local_model(self, model_name: str) -> Optional[Path]:
        """查找本地缓存的重排序模型路径。"""
        modelscope_id = self.MODEL_TO_MODELSCOPE_ID.get(model_name, model_name)
        candidates = [
            Path(model_name),
            self.backend_dir / model_name,
            self.cache_dir / model_name,
            self.cache_dir / model_name.replace("/", "_").replace(":", "_"),
            self.cache_dir / modelscope_id,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()

        if self.cache_dir.exists():
            for sub in self.cache_dir.rglob(model_name):
                if sub.is_dir():
                    return sub.resolve()
            # Also search for modelscope_id format
            for sub in self.cache_dir.rglob(modelscope_id):
                if sub.is_dir():
                    return sub.resolve()
        return None

    def _load_model(self, model_name: str) -> CrossEncoder:
        """加载 CrossEncoder 模型，优先使用本地缓存，否则从 ModelScope 下载（HF 兜底）。"""
        local_model = self._resolve_local_model(model_name)
        if local_model:
            logger.info("Loading reranker from local path: %s", local_model)
            return CrossEncoder(str(local_model))

        from app.utils.model_download import download_model

        logger.info("Downloading reranker via ModelScope (HuggingFace fallback): %s", model_name)
        try:
            model_path = download_model(model_name, self.cache_dir)
            return CrossEncoder(str(model_path))
        except Exception as exc:
            raise RuntimeError(
                "Failed to download reranker model. Download it manually via "
                "`modelscope download --model cross-encoder/ms-marco-MiniLM-L6-v2 "
                f"--local_dir data/models/cross-encoder/ms-marco-MiniLM-L6-v2`, "
                "or set ENABLE_RERANKER=false in .env"
            ) from exc

    def rerank(
        self,
        query: str,
        documents: List[dict],
        top_k: int = 3,
    ) -> List[Tuple[dict, float]]:
        """对文档列表按 query 相关性重新排序，返回 top_k 结果。

        预测失败时降级：返回原顺序（分数 0.0），由调用方按原序取 top_k，
        不让重排环节的错误中断问答。
        """
        if not documents:
            return []

        pairs = [[query, doc["content"]] for doc in documents]
        try:
            scores = self.model.predict(pairs)
        except Exception as e:  # noqa: BLE001
            logger.warning("reranker predict failed, skipping rerank: %s", e)
            return [(doc, 0.0) for doc in documents]

        ranked = sorted(
            zip(documents, scores),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_k]
