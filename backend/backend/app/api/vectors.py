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
    vs = request.app.state.vector_store
    chunks, total = vs.get_chunks(
        offset=offset, limit=limit, document_id=document_id, query=query,
    )
    return ChunkListResponse(chunks=chunks, total=total, offset=offset, limit=limit)
