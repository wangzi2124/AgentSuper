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
    kind: str = "multi-agent"
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
    """消息部件：text / reasoning / tool / file / patch / step。

    data 的结构由对应 *PartData 模型约束（见下），type 为该类的判别键。
    """

    id: str
    session_id: str
    message_id: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    time_created: int = 0


# ── Part data 结构（对齐 opencode Part 类型族）────────────────────────────
# 每个 type 对应一个 PartData 模型，写入 message_parts.data 前用其校验。

class PartDataText(BaseModel):
    """text：模型回答的文本片段。"""

    text: str = ""


class PartDataReasoning(BaseModel):
    """reasoning：模型思考/推理片段。"""

    text: str = ""


class PartDataTool(BaseModel):
    """tool：一次工具调用（含 pending/running/completed/error 状态机）。"""

    state: str = "completed"  # pending | running | completed | error
    name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    error: Optional[str] = None
    time_start: Optional[int] = None
    time_end: Optional[int] = None


class PartDataStep(BaseModel):
    """step-start / step-finish：处理步骤边界（检索/生成/压缩等）。"""

    state: str = "completed"  # running | completed | error
    name: str = ""
    detail: str = ""
    duration_ms: float = 0


class PartDataFile(BaseModel):
    """file：用户上传/引用的文件（mime + base64 或路径引用）。"""

    file: dict[str, Any] = Field(default_factory=dict)


class PartDataPatch(BaseModel):
    """patch：文件变更（统一 diff 文本）。"""

    patch: str = ""


class PartDataAgent(BaseModel):
    """agent：多 Agent 编排（子任务代理快照）。"""

    agent_id: str = ""
    agent_name: str = ""
    status: str = "completed"  # running | completed | failed


class PartDataCompaction(BaseModel):
    """compaction：历史压缩标记（auto/overflow + tail 起点）。"""

    mode: str = "auto"  # auto | overflow
    tail_start_id: Optional[str] = None


# type → PartData 模型的映射（append_part 校验/转换用）
PART_DATA_MODELS: dict[str, type[BaseModel]] = {
    "text": PartDataText,
    "reasoning": PartDataReasoning,
    "tool": PartDataTool,
    "step-start": PartDataStep,
    "step-finish": PartDataStep,
    "file": PartDataFile,
    "patch": PartDataPatch,
    "agent": PartDataAgent,
    "compaction": PartDataCompaction,
}


class TokenUsage(BaseModel):
    """单条消息的 token 结算（对齐 opencode Message.tokens）。"""

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0


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
    kind: str = "multi-agent"
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


class RevertRequest(BaseModel):
    """撤销到指定消息。"""

    message_id: str


class SessionStatus(BaseModel):
    """会话运行状态。"""

    session_id: str
    status: str
    queue_position: int = 0
