from typing import Annotated, Optional
from datetime import datetime
from pydantic import BaseModel, Field, StringConstraints


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
    index_state: Optional[str] = "ready"   # [D2] pending/processing/ready/failed
    index_error: Optional[str] = None      # [D2] 最近一次索引失败原因


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
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000)] = Field(..., description="用户消息内容（1~50000 字符，不能为空，首尾空白自动去除）")
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    use_vector_db: bool = False
    files: list[FileContent] = []
    directory: str = ""  # 会话绑定的工作目录（opencode ctx.directory），首条消息时创建会话用
    # [B4] 客户端消息幂等 id：前端自动/手动重试复用同一 id，
    # 服务端按 (user_id, session_id, client_msg_id) 去重，避免断网重试产生重复轮次。
    client_msg_id: Optional[str] = None


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


class MultiAgentChatResponse(BaseModel):
    """多 Agent 聊天响应模型。"""
    answer: str
    sources: list[Source]
    conversation_id: str
    steps: list[StepEvent] = []
    routed_to: Optional[str] = None