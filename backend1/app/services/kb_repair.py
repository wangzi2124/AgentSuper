# -*- coding: utf-8 -*-
"""知识库自愈服务（D2 跨库一致性）。

主库（authoritative）＝ FileStore：已保存的上传文件 + 元数据，
元数据中的 `index_state` 追踪派生索引构建进度：
  pending → processing → ready / failed

派生索引（derived）＝ Chroma（向量）+ BM25 + 章节库。上传链路
（fs.save → chunk → embed → vs/bm25/chapter add → ready）任何阶段崩溃，
主库与三套派生库都可能不一致，产生"孤儿数据"（文件在但向量缺失、
或向量在但元数据未回填）。

`repair_incomplete_documents` 遍历主库中 `index_state != "ready"` 的文档，
从已保存的文件重放 chunk → embed → vector/bm25/chapter，把三套派生库补齐到
与主库一致；写前按 document_id 幂等清理，重复自愈不会产生重复分块。

调用时机：
  1. 服务启动后一次性自愈（main.py lifespan）
  2. 后台维护循环定时执行（复用 vector_store_cleanup_interval_hours 间隔）
  3. 管理员手动触发 POST /api/vectors/repair
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _index_one(app_state, doc: dict) -> str:
    """同步重建单个文档的派生索引，供执行器线程调用。

    返回空串表示成功；非空为错误信息（主库已标记 failed，交由下次自愈重试）。
    """
    doc_id = doc["id"]
    fs = app_state.file_store
    stored_path = doc.get("path") or ""
    if not stored_path or not Path(stored_path).exists():
        fs.mark_index_state(doc_id, "failed", error="源文件缺失，无法重建索引")
        return "源文件缺失"

    vs = app_state.vector_store
    proc = app_state.doc_processor
    emb = app_state.embeddings
    bm25 = getattr(app_state, "bm25_index", None)
    cs = getattr(app_state, "chapter_store", None)
    filename = doc.get("filename", Path(stored_path).name)

    # 幂等预清理：先移除该文档可能残留的半成品派生数据（向量/章节/BM25）
    try:
        vs.delete_by_metadata("document_id", doc_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("KB repair pre-clean vectors failed for %s: %s", doc_id, e)
    if cs:
        try:
            cs.delete_by_document(doc_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("KB repair pre-clean chapters failed for %s: %s", doc_id, e)
    if bm25:
        try:
            bm25.remove_by_metadata("document_id", doc_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("KB repair pre-clean bm25 failed for %s: %s", doc_id, e)

    fs.mark_index_state(doc_id, "processing", chunk_count=0)
    try:
        chunks, chapter_metas = proc.process(stored_path, doc_id, filename)
        texts = [c[0] for c in chunks]
        metadatas = [c[1] for c in chunks]
        if not texts:
            raise ValueError("No text content could be extracted from the file")
        if cs and chapter_metas:
            for cm in chapter_metas:
                cs.add_chapter(
                    document_id=cm["document_id"],
                    filename=cm["filename"],
                    chapter_number=cm["chapter_number"],
                    chapter_title=cm["chapter_title"],
                    summary=cm["summary"],
                    parent_chunk_text=cm["parent_chunk_text"],
                )
        embeddings = emb.embed_documents(texts, 32, None)
        vs.add(texts, metadatas, embeddings)
        if bm25:
            bm25.add(texts, metadatas)
        fs.mark_index_state(doc_id, "ready", chunk_count=len(chunks))
        return ""
    except Exception as e:  # noqa: BLE001
        err = (str(e) or e.__class__.__name__)[:400]
        try:
            fs.mark_index_state(doc_id, "failed", error=err)
        except Exception:  # noqa: BLE001
            pass
        return err


def _collect_incomplete(app_state) -> list[dict]:
    """主库中所有 index_state != 'ready' 的文档（旧数据无该字段视作待修复）。"""
    return [
        doc for doc in app_state.file_store.list_all()
        if doc.get("index_state") != "ready"
    ]


async def repair_incomplete_documents(app_state) -> dict:
    """修复主库中所有索引未就绪的文档（幂等，可反复调用）。

    返回 {"repaired": [...], "failed": [...], "skipped": n}。
    串行锁（_doc_processing_lock）与上传/删除互斥，避免与在建索引打架。
    """
    incomplete = _collect_incomplete(app_state)
    if not incomplete:
        return {"repaired": [], "failed": [], "skipped": 0}

    from app.services.kb_cleanup import get_processing_lock

    async with get_processing_lock(app_state):
        repaired, failed = [], []
        for doc in incomplete:
            doc_id = doc["id"]
            err = await asyncio.to_thread(_index_one, app_state, doc)
            if err:
                failed.append({"id": doc_id, "error": err})
                logger.warning("KB repair failed for %s (%s): %s",
                               doc_id, doc.get("filename"), err)
            else:
                repaired.append(doc_id)
                logger.info("KB repair re-indexed %s (%s)",
                            doc_id, doc.get("filename"))
    return {
        "repaired": repaired,
        "failed": failed,
        "skipped": len(incomplete) - len(repaired),
    }