"""文档管理 API 路由模块。

提供文档上传、进度查询、列表获取和删除功能。
"""

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Request

from app.models.schemas import (
    DocumentResponse,
    DocumentListResponse,
    DeleteResponse,
    UploadResponse,
    TaskProgressResponse,
)

router = APIRouter()

# Maximum upload file size: 100 MB
MAX_UPLOAD_SIZE = 100 * 1024 * 1024
# 读取时单次分块大小（用于流式限制文件大小，避免整文件读入内存）
_CHUNK = 1024 * 1024


def _clean_upload_filename(filename: str) -> str:
    """清洗上传文件名，仅保留 basename，防止路径穿越。"""
    name = Path(filename or "").name
    if not name or name in (".", ".."):
        name = "unnamed"
    return name


def _get_doc_processing_lock(request: Request) -> asyncio.Lock:
    """获取文档处理串行锁（app 级单例）。

    上传的处理任务与删除都改写共享的 ChromaDB / BM25 / 章节库，
    串行化避免并发重建索引与删除交叉导致的数据错位。
    """
    lock = getattr(request.app.state, "_doc_processing_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state._doc_processing_lock = lock
    return lock


@router.post("/upload", response_model=UploadResponse)
async def upload_document(request: Request, file: UploadFile = File(...)):
    """上传文档并启动异步处理任务。"""
    filename = _clean_upload_filename(file.filename or "")

    # 流式分块读取，超过上限立即中止，避免大文件占满内存
    chunks: list[bytes] = []
    total = 0
    while True:
        data = await file.read(_CHUNK)
        if not data:
            break
        total += len(data)
        if total > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large: exceeds max {MAX_UPLOAD_SIZE} bytes (100 MB)"
            )
        chunks.append(data)
    content = b"".join(chunks)

    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    tm = request.app.state.task_manager
    task_id = tm.create(filename)

    bm25 = getattr(request.app.state, "bm25_index", None)

    async with _get_doc_processing_lock(request):
        task = asyncio.create_task(
            tm.process_document(
                task_id,
                content,
                filename,
                request.app.state.file_store,
                request.app.state.doc_processor,
                request.app.state.embeddings,
                request.app.state.vector_store,
                bm25,
                getattr(request.app.state, "chapter_store", None),
            )
        )
        # 保留引用防止被 GC（"Task was destroyed but it is pending!"），完成后自动移除
        pending = getattr(request.app.state, "_doc_processing_tasks", None)
        if pending is None:
            pending = request.app.state._doc_processing_tasks = set()
        pending.add(task)
        task.add_done_callback(pending.discard)

    return UploadResponse(task_id=task_id)


@router.get("/tasks/{task_id}", response_model=TaskProgressResponse)
async def get_task_progress(request: Request, task_id: str):
    """查询文档处理任务的进度状态。"""
    tm = request.app.state.task_manager
    task = tm.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    result = None
    if task.result:
        r = task.result
        created_at = r["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        result = DocumentResponse(
            id=r["id"],
            filename=r["filename"],
            size=r["size"],
            chunk_count=r["chunk_count"],
            created_at=created_at,
        )

    return TaskProgressResponse(
        task_id=task.task_id,
        filename=task.filename,
        status=task.status,
        progress=task.progress,
        stage=task.stage,
        result=result,
        error=task.error,
    )


@router.get("/", response_model=DocumentListResponse)
async def list_documents(request: Request):
    """获取所有已上传文档的列表。"""
    fs = request.app.state.file_store
    docs = fs.list_all()

    def safe_dt(doc: dict):
        """安全解析文档创建时间。"""
        try:
            return datetime.fromisoformat(doc["created_at"])
        except Exception:
            return datetime.now()

    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=d["id"],
                filename=d["filename"],
                size=d["size"],
                chunk_count=d.get("chunk_count", 0),
                created_at=safe_dt(d),
            )
            for d in docs
        ],
        total=len(docs),
    )


@router.delete("/{doc_id}", response_model=DeleteResponse)
async def delete_document(request: Request, doc_id: str):
    """删除指定文档及其关联的向量数据。"""
    fs = request.app.state.file_store
    vs = request.app.state.vector_store

    if not fs.get(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    async with _get_doc_processing_lock(request):
        fs.delete(doc_id)
        vs.delete_by_metadata("document_id", doc_id)
        cs = getattr(request.app.state, "chapter_store", None)
        if cs:
            cs.delete_by_document(doc_id)
        bm25 = getattr(request.app.state, "bm25_index", None)
        if bm25:
            await asyncio.to_thread(bm25.remove_by_metadata, "document_id", doc_id)
    return DeleteResponse(message="Document deleted")
