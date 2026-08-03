"""Session 领域的 Pydantic 模型。

对齐 opencode 的 Session.Info / SessionMessage / Part / Input 类型，
但使用本项目已有的 pydantic 约定。
"""

from typing import Any, Optional
from pydantic import BaseModel, Field


class ModelRef(BaseModel):
    """模型引用（对齐 opencode Model = {id, providerID, variant}）。"""

    id: str
    providerID: str
    variant: Optional[str] = None


class SessionInfo(BaseModel):
    """会话信息（对齐 opencode Session.Info 的核心字段）。"""

    id: str
    slug: str
    user_id: str
    project_id: str
    workspace_id: Optional[str] = None
    parent_id: Optional[str] = None
    directory: str = ""
    path: str = ""
    title: str = ""
    agent: Optional[str] = None
    model: Optional[ModelRef] = None
    kind: str = "chat"
    status: str = "idle"
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    time_created: int = 0
    time_updated: int = 0
    time_compacted: Optional[int] = None
    time_archived: Optional[int] = None


class Message(BaseModel):
    """会话消息（事件日志中的一行，seq 为会话内水位）。"""

    id: str
    session_id: str
    type: str  # user | assistant | system | compaction | epoch | tool
    role: Optional[str] = None  # 兼容旧格式 role 字段
    content: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    seq: int = 0
    time_created: int = 0


class Part(BaseModel):
    """消息部件：text / reasoning / tool / file / patch / step。"""

    id: str
    session_id: str
    message_id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    time_created: int = 0


class ContextEpoch(BaseModel):
    """per-session 系统上下文纪元（对齐 opencode SessionContextEpoch）。"""

    session_id: str
    baseline: str
    baseline_seq: int
    snapshot: dict[str, Any] = Field(default_factory=dict)


class SessionInput(BaseModel):
    """投递到会话的输入（对齐 opencode SessionInput delivery: steer|queue）。"""

    id: str
    session_id: str
    prompt: dict[str, Any] = Field(default_factory=dict)
    delivery: str = "steer"  # steer | queue
    admitted_seq: int = 0
    promoted_seq: Optional[int] = None


class ProjectInfo(BaseModel):
    """项目/工作区信息。"""

    id: str
    name: str = ""
    root: str
    vcs: str = ""


class SessionCreate(BaseModel):
    """创建会话请求体。"""

    project_id: Optional[str] = None
    parent_id: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[ModelRef] = None
    kind: str = "chat"
    title: Optional[str] = None
    directory: Optional[str] = None


class SessionUpdate(BaseModel):
    """更新会话请求体。"""

    title: Optional[str] = None
    archived: Optional[int] = None
    agent: Optional[str] = None
    model: Optional[ModelRef] = None


class PromptRequest(BaseModel):
    """向会话投递输入。"""

    prompt: str
    files: list[Any] = []
    delivery: str = "steer"  # steer | queue


class SessionStatus(BaseModel):
    """会话运行状态。"""

    session_id: str
    status: str
    queue_position: int = 0
