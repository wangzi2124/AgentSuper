from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class FileContent(BaseModel):
    """文件内容模型，用于接收上传的文件数据。"""
    filename: str
    data: str  # base64-encoded content
    mime_type: str


class DocumentResponse(BaseModel):
    """文档响应模型，返回文档的基本信息。"""
    id: str
    filename: str
    size: int
    chunk_count: int
    created_at: datetime


class DocumentListResponse(BaseModel):
    """文档列表响应模型，包含文档列表和总数。"""
    documents: list[DocumentResponse]
    total: int


class Source(BaseModel):
    """来源模型，表示检索到的文档片段来源。"""
    document_id: str
    content: str
    score: float


class StepEvent(BaseModel):
    """步骤事件模型，用于记录代理执行过程中的步骤信息。"""
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
    """聊天请求模型，包含用户消息和相关参数。"""
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    use_vector_db: bool = True
    files: list[FileContent] = []


class ChatResponse(BaseModel):
    """聊天响应模型，包含AI回答和相关源。"""
    answer: str
    sources: list[Source]
    conversation_id: str
    steps: list[StepEvent] = []


class UploadResponse(BaseModel):
    """上传响应模型，返回任务ID用于跟踪上传进度。"""
    task_id: str

class TaskProgressResponse(BaseModel):
    """任务进度响应模型，用于轮询文档处理进度。"""
    task_id: str
    filename: str
    status: str
    progress: int
    stage: str
    result: Optional[DocumentResponse] = None
    error: Optional[str] = None

class DeleteResponse(BaseModel):
    """删除响应模型，返回操作结果消息。"""
    message: str

class ChunkResponse(BaseModel):
    """文档块响应模型，返回单个文本块的信息。"""
    id: str
    text: str
    metadata: dict

class ChunkListResponse(BaseModel):
    """文档块列表响应模型，支持分页查询。"""
    chunks: list[ChunkResponse]
    total: int
    offset: int
    limit: int
