"""本地嵌入模型 — sentence-transformers 封装。

负责：
- 加载本地缓存的嵌入模型（BAAI/bge-small-zh-v1.5）
- 模型不存在时通过 ModelScope 自动下载
- 文本向量化（encode）
- 批量向量化支持

模型路径：
- 本地缓存：backend/data/models/
- 默认模型：BAAI/bge-small-zh-v1.5（中文优化）
- 英文备选：all-MiniLM-L6-v2
"""

import logging
from pathlib import Path
from sentence_transformers import SentenceTransformer
from typing import List, Optional

from app.utils.model_download import download_model

logger = logging.getLogger(__name__)


class LocalEmbeddings:
    """本地嵌入模型封装，支持本地缓存和远程下载。"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.backend_dir = Path(__file__).resolve().parents[2]
        self.local_cache_dir = self.backend_dir / "data" / "models"
        self.model = self._load_model(model_name)

    def _resolve_local_model(self, model_name: str) -> Optional[Path]:
        """查找本地缓存的嵌入模型路径。"""
        candidates = []

        model_path = Path(model_name)
        if model_path.is_absolute():
            candidates.append(model_path)
        else:
            candidates.append(Path(model_name))
            candidates.append(self.backend_dir / model_name)
            candidates.append(self.local_cache_dir / model_name)

            normalized = model_name.replace("/", "_").replace(":", "_")
            candidates.append(self.local_cache_dir / normalized)

        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
    
        if self.local_cache_dir.exists():
            for sub in self.local_cache_dir.rglob(model_name):
                if sub.is_dir() and not any(p.startswith(".") for p in sub.relative_to(self.local_cache_dir).parts):
                    return sub.resolve()    

        return None

    def _load_model(self, model_name: str) -> SentenceTransformer:
        """加载 SentenceTransformer 模型，优先本地，失败后远程下载。"""
        local_model = self._resolve_local_model(model_name)
        if local_model:
            return SentenceTransformer(str(local_model), device="cpu", local_files_only=True)

        try:
            model_path = download_model(model_name, self.local_cache_dir)
            return SentenceTransformer(str(model_path), device="cpu")
        except Exception as remote_error:
            if local_model:
                try:
                    return SentenceTransformer(str(local_model), device="cpu", local_files_only=True)
                except Exception as local_error:
                    logger.warning("Failed to load local embedding model %s: %s", local_model, local_error)
            raise RuntimeError(
                "Failed to load embedding model from remote source and no local fallback was found. "
                f"Set EMBEDDING_MODEL to a local path such as data/models/{model_name}"
            ) from remote_error

    def embed_documents(
        self, texts: List[str], batch_size: int = 32,
        on_progress: Optional[callable] = None,
    ) -> List[List[float]]:
        """批量生成文档文本的嵌入向量。"""
        if not texts:
            return []

        all_embeddings: List[List[float]] = []
        total = len(texts)
        for i in range(0, total, batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = self.model.encode(
                batch, batch_size=batch_size, show_progress_bar=False
            ).tolist()
            all_embeddings.extend(batch_embeddings)
            if on_progress:
                done = min(i + batch_size, total)
                on_progress(done, total)
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        """生成单条查询文本的嵌入向量。"""
        return self.model.encode([text], show_progress_bar=False)[0].tolist()
