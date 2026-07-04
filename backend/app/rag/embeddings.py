from pathlib import Path
from sentence_transformers import SentenceTransformer
from typing import List, Optional

from app.utils.model_download import download_model


class LocalEmbeddings:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.backend_dir = Path(__file__).resolve().parents[2]
        self.local_cache_dir = self.backend_dir / "data" / "models"
        self.model = self._load_model(model_name)

    def _resolve_local_model(self, model_name: str) -> Optional[Path]:
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
                except Exception:
                    pass
            raise RuntimeError(
                "Failed to load embedding model from remote source and no local fallback was found. "
                f"Set EMBEDDING_MODEL to a local path such as data/models/{model_name}"
            ) from remote_error

    def embed_documents(
        self, texts: List[str], batch_size: int = 32,
        on_progress: Optional[callable] = None,
    ) -> List[List[float]]:
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
        return self.model.encode([text], show_progress_bar=False)[0].tolist()
