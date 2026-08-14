"""知识库清理服务。

提供向量库 / 章节库 / BM25 / 上传文件的全量清空与按 TTL 过期清理，
并复用文档处理串行锁，避免与上传 / 删除并发产生数据错位。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional


def get_processing_lock(app_state) -> asyncio.Lock:
    """获取文档处理串行锁（app 级单例，与 documents.py 共用同一把锁）。"""
    lock = getattr(app_state, "_doc_processing_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app_state._doc_processing_lock = lock
    return lock


def delete_document_data(app_state, doc_id: str) -> None:
    """删除单个文档的级联数据：上传文件 + 向量块 + 章节 + BM25 条目。"""
    fs = app_state.file_store
    if not fs.get(doc_id):
        return
    fs.delete(doc_id)
    app_state.vector_store.delete_by_metadata("document_id", doc_id)
    cs = getattr(app_state, "chapter_store", None)
    if cs:
        cs.delete_by_document(doc_id)
    bm25 = getattr(app_state, "bm25_index", None)
    if bm25:
        bm25.remove_by_metadata("document_id", doc_id)


async def clear_all_kb(app_state) -> dict:
    """清空全部知识库数据（向量库 + 章节库 + BM25 + 上传文件）。"""
    async with get_processing_lock(app_state):
        removed_vectors = app_state.vector_store.clear_all()
        cs = getattr(app_state, "chapter_store", None)
        removed_chapters = cs.clear_all() if cs else 0
        bm25 = getattr(app_state, "bm25_index", None)
        if bm25:
            bm25.clear()
        removed_docs = app_state.file_store.clear_all()
    return {
        "removed_vectors": removed_vectors,
        "removed_chapters": removed_chapters,
        "removed_documents": removed_docs,
    }


async def clear_expired(app_state, now: Optional[datetime] = None) -> int:
    """按 TTL 清理过期文档（以文档创建时间计），返回清理数量。"""
    from app.config import settings

    ttl_days = settings.vector_store_ttl_days
    if ttl_days <= 0:
        return 0
    cutoff = (now or datetime.now()) - timedelta(days=ttl_days)
    expired = []
    for doc in app_state.file_store.list_all():
        try:
            created = datetime.fromisoformat(doc["created_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if created < cutoff:
            expired.append(doc["id"])
    if not expired:
        return 0
    async with get_processing_lock(app_state):
        for doc_id in expired:
            delete_document_data(app_state, doc_id)
    return len(expired)
