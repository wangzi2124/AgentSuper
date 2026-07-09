import json
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class FileStore:
    def __init__(self, upload_dir: str):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.upload_dir / "metadata.json"
        self.metadata: dict = {}
        self._load_metadata()

    def _load_metadata(self):
        if self.meta_path.exists():
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load metadata from %s: %s", self.meta_path, e)
                self.metadata = {}

    def _save_metadata(self):
        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error("Failed to save metadata to %s: %s", self.meta_path, e)

    def save(self, filename: str, content: bytes) -> tuple[str, str]:
        doc_id = str(uuid.uuid4())
        safe_name = f"{doc_id}_{filename}"
        file_path = self.upload_dir / safe_name
        with open(file_path, "wb") as f:
            f.write(content)
        self.metadata[doc_id] = {
            "filename": filename,
            "path": str(file_path),
            "created_at": datetime.now().isoformat(),
            "size": len(content),
        }
        self._save_metadata()
        return doc_id, str(file_path)

    def delete(self, doc_id: str):
        if doc_id not in self.metadata:
            return
        path = Path(self.metadata[doc_id]["path"])
        if path.exists():
            path.unlink()
        del self.metadata[doc_id]
        self._save_metadata()

    def update_meta(self, doc_id: str, updates: dict):
        if doc_id in self.metadata:
            self.metadata[doc_id].update(updates)
            self._save_metadata()

    def get(self, doc_id: str) -> Optional[dict]:
        return self.metadata.get(doc_id)

    def list_all(self) -> list[dict]:
        return [
            {"id": doc_id, **meta}
            for doc_id, meta in self.metadata.items()
        ]
