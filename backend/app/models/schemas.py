from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class FileContent(BaseModel):
    filename: str
    data: str  # base64-encoded content
    mime_type: str


class DocumentResponse(BaseModel):
    id: str
    filename: str
    size: int
    chunk_count: int
    created_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


class Source(BaseModel):
    document_id: str
    content: str
    score: float


class StepEvent(BaseModel):
    type: str  # "step_start" | "step_end" | "tool_start" | "tool_end"
    step_id: str
    name: str
    status: str  # "running" | "completed" | "failed"
    detail: Optional[str] = None
    duration_ms: Optional[float] = None
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    use_vector_db: bool = True
    files: list[FileContent] = []


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    conversation_id: str
    steps: list[StepEvent] = []


class UploadResponse(BaseModel):
    task_id: str

class TaskProgressResponse(BaseModel):
    task_id: str
    filename: str
    status: str
    progress: int
    stage: str
    result: Optional[DocumentResponse] = None
    error: Optional[str] = None

class DeleteResponse(BaseModel):
    message: str

class ChunkResponse(BaseModel):
    id: str
    text: str
    metadata: dict

class ChunkListResponse(BaseModel):
    chunks: list[ChunkResponse]
    total: int
    offset: int
    limit: int
