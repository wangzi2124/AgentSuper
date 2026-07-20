"""向量存储 API 路由模块。

提供向量数据库中文档分块的查询功能。
"""

from fastapi import APIRouter, HTTPException, Request, Query

from app.models.schemas import ChunkListResponse

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
