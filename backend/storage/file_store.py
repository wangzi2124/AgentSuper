import json
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _safe_filename(filename: str) -> str:
    """清洗上传文件名，丢弃路径分隔符与相对路径片段，防止路径穿越。"""
    name = Path(filename or "").name
    if not name or name in (".", ".."):
        name = "unnamed"
    return name


class FileStore:
    """文件存储管理器，负责文件的保存、删除和元数据管理。"""

    def __init__(self, upload_dir: str):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.upload_dir / "metadata.json"
        self.metadata: dict = {}
        self._load_metadata()

    def _load_metadata(self):
        """从JSON文件加载文档元数据。"""
        if self.meta_path.exists():
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load metadata from %s: %s", self.meta_path, e)
                self.metadata = {}

    def _save_metadata(self):
        """将文档元数据保存到JSON文件。"""
        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error("Failed to save metadata to %s: %s", self.meta_path, e)

    def save(self, filename: str, content: bytes) -> tuple[str, str]:
        """保存文件并更新元数据，返回文档ID和文件路径。

        index_state 记录派生索引（向量/BM25/章节）的构建进度，作为跨库一致性
        的主库状态：pending → processing → ready / failed。任一索引阶段崩溃后，
        自愈流程（kb_repair）根据该状态从已保存的文件重放建索引。
        """
        doc_id = str(uuid.uuid4())
        safe_name = f"{doc_id}_{_safe_filename(filename)}"
        file_path = self.upload_dir / safe_name
        with open(file_path, "wb") as f:
            f.write(content)
        self.metadata[doc_id] = {
            "filename": filename,
            "path": str(file_path),
            "created_at": datetime.now().isoformat(),
            "size": len(content),
            "chunk_count": 0,
            "index_state": "pending",
            "index_error": None,
        }
        self._save_metadata()
        return doc_id, str(file_path)

    def delete(self, doc_id: str):
        """删除指定文档的文件和元数据。"""
        if doc_id not in self.metadata:
            return
        path = Path(self.metadata[doc_id]["path"])
        if path.exists():
            path.unlink()
        del self.metadata[doc_id]
        self._save_metadata()

    def update_meta(self, doc_id: str, updates: dict):
        """更新指定文档的元数据。"""
        if doc_id in self.metadata:
            self.metadata[doc_id].update(updates)
            self._save_metadata()

    def mark_index_state(self, doc_id: str, state: str, error: Optional[str] = None,
                         chunk_count: Optional[int] = None):
        """更新派生索引状态（pending/processing/ready/failed）。

        state='ready' 时表示向量库+BM25+章节库均已写入；其余状态提示自愈流程
        需要从主库（本文件 + 元数据）重放建索引。
        """
        if doc_id not in self.metadata:
            return
        meta = self.metadata[doc_id]
        meta["index_state"] = state
        meta["index_error"] = error
        if chunk_count is not None:
            meta["chunk_count"] = chunk_count
        self.metadata[doc_id] = meta
        self._save_metadata()

    def get(self, doc_id: str) -> Optional[dict]:
        """获取指定文档的元数据。"""
        return self.metadata.get(doc_id)

    def clear_all(self) -> int:
        """清空所有上传文件及元数据，返回删除数量。"""
        count = len(self.metadata)
        for meta in self.metadata.values():
            path = Path(meta.get("path", ""))
            if path and path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning("Failed to remove file %s: %s", path, e)
        self.metadata = {}
        self._save_metadata()
        return count

    def list_all(self) -> list[dict]:
        """列出所有文档的元数据信息。"""
        return [
            {"id": doc_id, **meta}
            for doc_id, meta in self.metadata.items()
        ]
