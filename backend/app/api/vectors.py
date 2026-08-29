"""向量存储 API 路由模块。

提供向量数据库中文档分块的查询功能，以及向量库 / 章节库的全量清空与 TTL 清理。
"""

from fastapi import APIRouter, HTTPException, Request, Query

from app.api.deps import require_admin
from app.config import settings
from app.models.schemas import ChunkListResponse
from app.services.kb_cleanup import clear_all_kb, clear_expired

router = APIRouter()

@router.get("/", response_model=ChunkListResponse)
async def list_chunks(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    document_id: str = Query(None),
    query: str = Query(None, description="Full-text search within chunk content"),
):
    """获取向量数据库中的文档分块列表，支持分页和全文搜索。"""
    vs = request.app.state.vector_store
    chunks, total = vs.get_chunks(
        offset=offset, limit=limit, document_id=document_id, query=query,
    )
    return ChunkListResponse(chunks=chunks, total=total, offset=offset, limit=limit)


@router.get("/config")
async def vector_store_config(request: Request):
    """返回向量库清理相关配置 + 索引一致性统计（修复按钮数据来源）。"""
    fs = request.app.state.file_store
    states: dict[str, int] = {}
    for doc in fs.list_all():
        st = doc.get("index_state", "pending")  # 旧数据无该字段视作待修复
        states[st] = states.get(st, 0) + 1
    return {
        "auto_clear": settings.vector_store_auto_clear,
        "ttl_days": settings.vector_store_ttl_days,
        "cleanup_interval_hours": settings.vector_store_cleanup_interval_hours,
        "count": request.app.state.vector_store.count,
        "index_states": states,
        "pending_repair": states.get("pending", 0) + states.get("processing", 0) + states.get("failed", 0),
    }


@router.post("/clear")
async def clear_vector_store(request: Request):
    """清空全部知识库数据（向量库 + 章节库 + BM25 + 上传文件）。"""
    require_admin(request)
    result = await clear_all_kb(request.app.state)
    return {
        "message": "向量库、章节库与上传文件已全部清空",
        "removed_vectors": result["removed_vectors"],
        "removed_chapters": result["removed_chapters"],
        "removed_documents": result["removed_documents"],
    }


@router.post("/clear-expired")
async def clear_expired_vectors(request: Request):
    """按 TTL 配置手动触发过期文档清理。"""
    require_admin(request)
    removed = await clear_expired(request.app.state)
    return {
        "message": f"已清理 {removed} 个过期文档",
        "removed": removed,
        "ttl_days": settings.vector_store_ttl_days,
    }


@router.post("/repair")
async def repair_vectors(request: Request):
    """自愈重建：对 index_state != ready 的文档重放建索引（向量/BM25/章节）。

    上传/重建崩溃遗留的孤儿数据在此补齐：主库（已保存文件+元数据）为权威，
    派生索引按 document_id 幂等重建，多次调用安全。"""
    require_admin(request)
    from app.services.kb_repair import repair_incomplete_documents

    result = await repair_incomplete_documents(request.app.state)
    repaired = len(result["repaired"])
    failed = len(result["failed"])
    message = "索引已全部一致，无需修复" if not repaired and not failed else (
        f"已重建 {repaired} 个文档的索引" + (f"，{failed} 个失败" if failed else "")
    )
    return {
        "message": message,
        **result,
    }
