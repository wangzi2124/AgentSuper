"""重排序（对齐 agent.md §4.1 / §4.2）。

自包含 CrossEncoder 重排器（本地缓存优先，ModelScope 下载 + HuggingFace 兜底）；
模型不可用时降级为不重排（保持原顺序），绝不让重排环节中断问答。

本模块不依赖 `app.*`（仅 `sentence_transformers` + `utils.model_download`）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MODEL_TO_MODELSCOPE_ID = {
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "cross-encoder/ms-marco-MiniLM-L6-v2",
}


class RerankerUnavailable(RuntimeError):
    """重排模型不可用（缺依赖 / 缺模型文件）。"""


class CrossEncoderReranker:
    """基于 sentence-transformers CrossEncoder 的重排实现（自包含）。"""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: Optional[Path] = None) -> None:
        self.model_name = model_name
        self.backend_dir = Path(__file__).resolve().parents[1]
        from storage.paths import data_path

        self.cache_dir = Path(cache_dir) if cache_dir else data_path(None, "models")
        self.model = self._load_model(model_name)

    def _resolve_local_model(self, model_name: str) -> Optional[Path]:
        modelscope_id = _MODEL_TO_MODELSCOPE_ID.get(model_name, model_name)
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
            for sub in self.cache_dir.rglob(modelscope_id):
                if sub.is_dir():
                    return sub.resolve()
        return None

    def _load_model(self, model_name: str) -> Any:
        from sentence_transformers import CrossEncoder

        local_model = self._resolve_local_model(model_name)
        if local_model:
            logger.info("Loading reranker from local path: %s", local_model)
            return CrossEncoder(str(local_model))

        from utils.model_download import download_model

        logger.info("Downloading reranker via ModelScope (HuggingFace fallback): %s", model_name)
        try:
            model_path = download_model(model_name, self.cache_dir)
            return CrossEncoder(str(model_path))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Failed to download reranker model. Download it manually via "
                "`modelscope download --model cross-encoder/ms-marco-MiniLM-L6-v2 "
                f"--local_dir data/models/cross-encoder/ms-marco-MiniLM-L6-v2`, "
                "or set enable_reranker=false in config.json"
            ) from exc

    def rerank(
        self, query: str, documents: List[dict], top_k: int = 3
    ) -> List[Tuple[dict, float]]:
        if not documents:
            return []
        pairs = [[query, doc["content"]] for doc in documents]
        try:
            scores = self.model.predict(pairs)
        except Exception as e:  # noqa: BLE001
            logger.warning("reranker predict failed, skipping rerank: %s", e)
            return [(doc, 0.0) for doc in documents]
        ranked = sorted(zip(documents, scores), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class Reranker:
    """认知层重排器：懒加载底层模型，失败即降级为恒等重排。"""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        top_k: int = 3,
        enabled: bool = True,
    ) -> None:
        self.model_name = model_name
        self.top_k = top_k
        self.enabled = enabled
        self._impl: Any = None
        self._load_failed = False

    def _ensure(self) -> Any:
        if self._impl is not None or self._load_failed or not self.enabled:
            return self._impl
        try:
            self._impl = CrossEncoderReranker(self.model_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker unavailable, degrading to identity: %s", exc)
            self._load_failed = True
        return self._impl

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """重排并返回带 `rerank_score` 的前 top_k 条。失败时原顺序。"""
        k = top_k or self.top_k
        if not documents:
            return []
        impl = self._ensure()
        if impl is None:
            return [dict(d, rerank_score=d.get("score", 0.0)) for d in documents[:k]]

        candidates = [{"content": d.get("content", ""), **{k2: v for k2, v in d.items() if k2 != "content"}} for d in documents]
        try:
            ranked = impl.rerank(query, candidates, top_k=k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rerank failed, identity order: %s", exc)
            return [dict(d, rerank_score=d.get("score", 0.0)) for d in documents[:k]]

        out: list[dict[str, Any]] = []
        for doc, score in ranked:
            merged = dict(doc)
            merged["rerank_score"] = float(score)
            out.append(merged)
        return out

    @property
    def available(self) -> bool:
        return self.enabled and self._ensure() is not None
