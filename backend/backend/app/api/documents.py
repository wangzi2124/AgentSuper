from datetime import datetime

from fastapi import APIRouter, UploadFile, File, HTTPException, Request

from app.models.schemas import (
    DocumentResponse,
    DocumentListResponse,
    DeleteResponse,
    UploadResponse,
    TaskProgressResponse,
)

router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_document(request: Request, file: UploadFile = File(...)):
    content = await file.read()

    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    tm = request.app.state.task_manager
    task_id = tm.create(file.filename)

    bm25 = getattr(request.app.state, "bm25_index", None)

    import asyncio
    asyncio.create_task(
        tm.process_document(
            task_id,
            content,
            file.filename,
            request.app.state.file_store,
            request.app.state.doc_processor,
            request.app.state.embeddings,
            request.app.state.vector_store,
            bm25,
            getattr(request.app.state, "chapter_store", None),
        )
    )

    return UploadResponse(task_id=task_id)


@router.get("/tasks/{task_id}", response_model=TaskProgressResponse)
async def get_task_progress(request: Request, task_id: str):
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
    fs = request.app.state.file_store
    docs = fs.list_all()

    def safe_dt(doc: dict):
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
    fs = request.app.state.file_store
    vs = request.app.state.vector_store

    if not fs.get(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    fs.delete(doc_id)
    vs.delete_by_metadata("document_id", doc_id)
    cs = getattr(request.app.state, "chapter_store", None)
    if cs:
        cs.delete_by_document(doc_id)
    return DeleteResponse(message="Document deleted")
