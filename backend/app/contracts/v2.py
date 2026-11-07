"""Web Chat v2 契约（Phase 0）。

这里只定义跨端 JSON 契约，不接线任何运行时。
字段统一使用 snake_case；语义对齐 OpenCode 的 Session/Message/Part/Event。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

Delivery = Literal["steer", "queue"]
SessionState = Literal["idle", "busy", "retry"]
SessionOutcome = Literal["succeeded", "failed", "interrupted"]
ToolStatus = Literal["streaming", "running", "completed", "error"]
PermissionReply = Literal["once", "always", "reject"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRef(StrictModel):
    id: str
    provider_id: str = ""
    variant: str | None = None


class TokenUsage(StrictModel):
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0


class SessionTime(StrictModel):
    created: int
    updated: int
    busy: int | None = None
    idle: int | None = None
    viewed: int | None = None
    archived: int | None = None


class LocationRef(StrictModel):
    directory: str
    project_id: str | None = None


class SessionInfoV2(StrictModel):
    id: str
    parent_id: str | None = None
    project_id: str
    directory: str
    title: str = ""
    agent: str | None = None
    model: ModelRef | None = None
    status: SessionState = "idle"
    outcome: SessionOutcome | None = None
    cost: float = 0.0
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    time: SessionTime
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredError(StrictModel):
    type: str
    message: str
    status: int | None = None
    retryable: bool | None = None
    provider: str | None = None
    code: str | None = None


class FileAttachment(StrictModel):
    type: Literal["file"] = "file"
    uri: str
    mime: str | None = None
    name: str | None = None
    data: str | None = None


class AgentAttachment(StrictModel):
    type: Literal["agent"] = "agent"
    id: str


class SkillAttachment(StrictModel):
    type: Literal["skill"] = "skill"
    id: str


class VoiceAttachment(StrictModel):
    id: str
    url: str
    duration: float = 0.0
    waveform: list[float] = Field(default_factory=list)
    text: str = ""


class PromptInput(StrictModel):
    id: str | None = None
    text: str
    files: list[FileAttachment] = Field(default_factory=list)
    agents: list[AgentAttachment] = Field(default_factory=list)
    skills: list[SkillAttachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    voice: VoiceAttachment | None = None
    delivery: Delivery = "steer"
    resume: bool = True


class TextToolContent(StrictModel):
    type: Literal["text"] = "text"
    text: str


class FileToolContent(StrictModel):
    type: Literal["file"] = "file"
    uri: str
    mime: str | None = None
    name: str | None = None


ToolContent = Annotated[
    Union[TextToolContent, FileToolContent],
    Field(discriminator="type"),
]


class ToolStateStreaming(StrictModel):
    status: Literal["streaming"] = "streaming"
    input: str


class ToolStateRunning(StrictModel):
    status: Literal["running"] = "running"
    input: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolStateCompleted(StrictModel):
    status: Literal["completed"] = "completed"
    input: dict[str, Any]
    content: list[ToolContent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolStateError(StrictModel):
    status: Literal["error"] = "error"
    input: dict[str, Any]
    error: StructuredError
    content: list[ToolContent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


ToolState = Annotated[
    Union[ToolStateStreaming, ToolStateRunning, ToolStateCompleted, ToolStateError],
    Field(discriminator="status"),
]


class TextPart(StrictModel):
    type: Literal["text"] = "text"
    text: str
    native: dict[str, Any] | None = None


class ReasoningPart(StrictModel):
    type: Literal["reasoning"] = "reasoning"
    text: str
    native: dict[str, Any] | None = None


class ToolPart(StrictModel):
    type: Literal["tool"] = "tool"
    id: str
    name: str
    state: ToolState
    time_created: int | None = None


MessagePart = Annotated[
    Union[TextPart, ReasoningPart, ToolPart],
    Field(discriminator="type"),
]


class UserMessage(StrictModel):
    type: Literal["user"] = "user"
    id: str
    time_created: int
    text: str
    files: list[FileAttachment] = Field(default_factory=list)
    agents: list[AgentAttachment] = Field(default_factory=list)
    skills: list[SkillAttachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    voice: VoiceAttachment | None = None


class AssistantMessage(StrictModel):
    type: Literal["assistant"] = "assistant"
    id: str
    time_created: int
    time_completed: int | None = None
    agent: str | None = None
    model: ModelRef | None = None
    content: list[MessagePart] = Field(default_factory=list)
    finish: str | None = None
    cost: float | None = None
    tokens: TokenUsage | None = None
    error: StructuredError | None = None


class SyntheticMessage(StrictModel):
    type: Literal["synthetic"] = "synthetic"
    id: str
    time_created: int
    text: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemMessage(StrictModel):
    type: Literal["system"] = "system"
    id: str
    time_created: int
    text: str


class SkillMessage(StrictModel):
    type: Literal["skill"] = "skill"
    id: str
    time_created: int
    skill: str
    name: str
    text: str


class ShellOutput(StrictModel):
    output: str = ""
    cursor: int = 0
    size: int = 0
    truncated: bool = False


class ShellMessage(StrictModel):
    type: Literal["shell"] = "shell"
    id: str
    time_created: int
    shell_id: str
    command: str
    status: Literal["running", "exited", "timeout", "killed"]
    exit: int | None = None
    output: ShellOutput | None = None


class CompactionMessage(StrictModel):
    type: Literal["compaction"] = "compaction"
    id: str
    time_created: int
    status: Literal["running", "completed", "failed"]
    reason: Literal["auto", "manual"]
    summary: str = ""
    model: ModelRef | None = None
    cost: float | None = None
    tokens: TokenUsage | None = None
    error: StructuredError | None = None


class IdleMessage(StrictModel):
    type: Literal["idle"] = "idle"
    id: str
    time_created: int
    outcome: SessionOutcome
    error: StructuredError | None = None


class AgentSelectedMessage(StrictModel):
    type: Literal["agent-switched"] = "agent-switched"
    id: str
    time_created: int
    agent: str


class ModelSelectedMessage(StrictModel):
    type: Literal["model-switched"] = "model-switched"
    id: str
    time_created: int
    model: ModelRef


class LocationSwitchedMessage(StrictModel):
    type: Literal["location-switched"] = "location-switched"
    id: str
    time_created: int
    location: LocationRef


MessageV2 = Annotated[
    Union[
        UserMessage,
        AssistantMessage,
        SyntheticMessage,
        SystemMessage,
        SkillMessage,
        ShellMessage,
        CompactionMessage,
        IdleMessage,
        AgentSelectedMessage,
        ModelSelectedMessage,
        LocationSwitchedMessage,
    ],
    Field(discriminator="type"),
]
MESSAGE_ADAPTER = TypeAdapter(MessageV2)
MESSAGE_LIST_ADAPTER = TypeAdapter(list[MessageV2])
MESSAGE_PART_LIST_ADAPTER = TypeAdapter(list[MessagePart])


class InboxUserPayload(StrictModel):
    text: str
    files: list[FileAttachment] = Field(default_factory=list)
    agents: list[AgentAttachment] = Field(default_factory=list)
    skills: list[SkillAttachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InboxCompactionPayload(StrictModel):
    reason: Literal["auto", "manual"] = "manual"


class InboxMovePayload(StrictModel):
    location: LocationRef
    project_id: str | None = None


class InboxUserItem(StrictModel):
    type: Literal["user"] = "user"
    payload: InboxUserPayload
    delivery: Delivery = "steer"


class InboxCompactionItem(StrictModel):
    type: Literal["compaction"] = "compaction"
    payload: InboxCompactionPayload
    delivery: Delivery = "steer"


class InboxMoveItem(StrictModel):
    type: Literal["move"] = "move"
    payload: InboxMovePayload
    delivery: Delivery = "queue"


InboxItem = Annotated[
    Union[InboxUserItem, InboxCompactionItem, InboxMoveItem],
    Field(discriminator="type"),
]


class InboxInfo(StrictModel):
    id: str
    session_id: str
    item: InboxItem
    status: Literal["pending", "delivered", "cancelled"]
    admitted_seq: int = 0
    promoted_seq: int | None = None
    time_created: int
    time_delivered: int | None = None


class PermissionSource(StrictModel):
    type: Literal["tool"] = "tool"
    message_id: str
    id: str


class PermissionRequest(StrictModel):
    id: str
    session_id: str
    action: str
    resources: list[str] = Field(default_factory=list)
    save: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: PermissionSource | None = None
    message: str | None = None


class PermissionReplied(StrictModel):
    session_id: str
    request_id: str
    reply: PermissionReply


class FormField(StrictModel):
    id: str
    type: str
    label: str = ""
    required: bool = False
    options: list[str] = Field(default_factory=list)


class FormInfo(StrictModel):
    id: str
    session_id: str
    title: str = ""
    fields: list[FormField] = Field(default_factory=list)
    state: Literal["pending", "replied", "cancelled"] = "pending"
    response: dict[str, Any] = Field(default_factory=dict)


class RevertState(StrictModel):
    message_id: str
    files: bool = False


class SessionStatusState(StrictModel):
    type: SessionState
    attempt: int | None = None
    message: str | None = None
    next: int | None = None


class DurableRef(StrictModel):
    aggregate_id: str
    seq: int
    version: int = 2


class SessionCreatedData(StrictModel):
    session: SessionInfoV2


class SessionRenamedData(StrictModel):
    session_id: str
    title: str


class SessionRefData(StrictModel):
    session_id: str


class SessionMovedData(StrictModel):
    session_id: str
    location: LocationRef
    project_id: str | None = None


class SessionForkedData(StrictModel):
    session_id: str
    parent_id: str
    boundary: str | None = None


class SessionUpdatedData(StrictModel):
    session_id: str
    title: str | None = None
    status: SessionState | None = None


class SessionStatusData(StrictModel):
    session_id: str
    status: SessionStatusState


class SessionIdleData(StrictModel):
    session_id: str
    outcome: SessionOutcome | None = None


class ExecutionFailedData(StrictModel):
    session_id: str
    error: StructuredError


class ExecutionInterruptedData(StrictModel):
    session_id: str
    reason: Literal["user", "shutdown", "superseded", "inactivity"]


class RetryScheduledData(StrictModel):
    session_id: str
    assistant_message_id: str
    attempt: int
    at: int
    error: StructuredError


class InboxEnqueuedData(StrictModel):
    session_id: str
    inbox_id: str
    item: InboxItem


class InboxDeliveredData(StrictModel):
    session_id: str
    inbox_id: str


class InboxDeliveryChangedData(StrictModel):
    session_id: str
    inbox_id: str
    delivery: Delivery


class StepStartedData(StrictModel):
    session_id: str
    assistant_message_id: str
    agent: str | None = None
    model: ModelRef | None = None
    started: int


class StepStreamedData(StrictModel):
    session_id: str
    assistant_message_id: str


class StepEndedData(StrictModel):
    session_id: str
    assistant_message_id: str
    finish: str | None = None
    cost: float | None = None
    tokens: TokenUsage | None = None


class StepFailedData(StrictModel):
    session_id: str
    assistant_message_id: str
    error: StructuredError
    finish: str | None = None
    cost: float | None = None
    tokens: TokenUsage | None = None


class TextStartedData(StrictModel):
    session_id: str
    assistant_message_id: str
    ordinal: int


class TextDeltaData(StrictModel):
    session_id: str
    assistant_message_id: str
    ordinal: int
    delta: str


class TextEndedData(StrictModel):
    session_id: str
    assistant_message_id: str
    ordinal: int
    text: str
    native: dict[str, Any] | None = None


class ToolInputStartedData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    name: str


class ToolInputDeltaData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    delta: str


class ToolInputEndedData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    text: str


class ToolCalledData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    name: str = ""
    input: dict[str, Any]
    executed: bool = False


class ToolProgressData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolSuccessData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    name: str = ""
    content: list[ToolContent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    executed: bool = True


class ToolFailedData(StrictModel):
    session_id: str
    assistant_message_id: str
    id: str
    name: str = ""
    error: StructuredError
    content: list[ToolContent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    executed: bool = True


class CompactionStartedData(StrictModel):
    session_id: str
    reason: Literal["auto", "manual"]
    input_id: str | None = None


class CompactionDeltaData(StrictModel):
    session_id: str
    text: str


class CompactionEndedData(StrictModel):
    session_id: str
    reason: Literal["auto", "manual"]
    text: str
    model: ModelRef | None = None
    cost: float | None = None
    tokens: TokenUsage | None = None


class CompactionFailedData(StrictModel):
    session_id: str
    reason: Literal["auto", "manual"]
    error: StructuredError
    input_id: str | None = None
    cost: float | None = None
    tokens: TokenUsage | None = None


class MessageContentUpdatedData(StrictModel):
    session_id: str
    message_id: str
    content: list[MessagePart] = Field(default_factory=list)


class UsageUpdatedData(StrictModel):
    session_id: str
    cost: float
    tokens: TokenUsage


class UsageRecordedData(StrictModel):
    session_id: str
    source: Literal["title", "compaction"]
    cost: float
    tokens: TokenUsage


class SyntheticData(StrictModel):
    session_id: str
    text: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillActivatedData(StrictModel):
    session_id: str
    id: str
    name: str
    text: str


class ShellStartedData(StrictModel):
    session_id: str
    shell: dict[str, Any]


class ShellEndedData(StrictModel):
    session_id: str
    shell: dict[str, Any]
    output: ShellOutput


class RevertStagedData(StrictModel):
    session_id: str
    revert: RevertState


class RevertCommittedData(StrictModel):
    session_id: str
    revert: RevertState


class ModelSelectedData(StrictModel):
    session_id: str
    model: ModelRef


class AgentSelectedData(StrictModel):
    session_id: str
    agent: str


class FormRepliedData(StrictModel):
    session_id: str
    form_id: str
    response: dict[str, Any] = Field(default_factory=dict)


class FormRefData(StrictModel):
    session_id: str
    form_id: str


class ServerConnectedData(StrictModel):
    version: str | None = None


EventType = Literal[
    "server.connected",
    "session.created",
    "session.renamed",
    "session.archived",
    "session.unarchived",
    "session.moved",
    "session.deleted",
    "session.forked",
    "session.viewed",
    "session.updated",
    "session.status",
    "session.idle",
    "session.execution.started",
    "session.execution.succeeded",
    "session.execution.failed",
    "session.execution.interrupted",
    "session.retry.scheduled",
    "session.inbox.enqueued",
    "session.inbox.delivered",
    "session.inbox.cancelled",
    "session.inbox.delivery.changed",
    "session.step.started",
    "session.step.streamed",
    "session.step.ended",
    "session.step.failed",
    "session.text.started",
    "session.text.delta",
    "session.text.ended",
    "session.reasoning.started",
    "session.reasoning.delta",
    "session.reasoning.ended",
    "session.tool.input.started",
    "session.tool.input.delta",
    "session.tool.input.ended",
    "session.tool.called",
    "session.tool.progress",
    "session.tool.success",
    "session.tool.failed",
    "session.compaction.started",
    "session.compaction.delta",
    "session.compaction.ended",
    "session.compaction.failed",
    "session.message.content.updated",
    "session.usage.updated",
    "session.usage.recorded",
    "session.synthetic",
    "session.skill.activated",
    "session.shell.started",
    "session.shell.ended",
    "session.revert.staged",
    "session.revert.committed",
    "session.revert.cleared",
    "session.model.selected",
    "session.agent.selected",
    "permission.asked",
    "permission.replied",
    "form.created",
    "form.replied",
    "form.cancelled",
]


EVENT_DATA_MODELS: dict[str, type[StrictModel]] = {
    "server.connected": ServerConnectedData,
    "session.created": SessionCreatedData,
    "session.renamed": SessionRenamedData,
    "session.archived": SessionRefData,
    "session.unarchived": SessionRefData,
    "session.moved": SessionMovedData,
    "session.deleted": SessionRefData,
    "session.forked": SessionForkedData,
    "session.viewed": SessionRefData,
    "session.updated": SessionUpdatedData,
    "session.status": SessionStatusData,
    "session.idle": SessionIdleData,
    "session.execution.started": SessionRefData,
    "session.execution.succeeded": SessionRefData,
    "session.execution.failed": ExecutionFailedData,
    "session.execution.interrupted": ExecutionInterruptedData,
    "session.retry.scheduled": RetryScheduledData,
    "session.inbox.enqueued": InboxEnqueuedData,
    "session.inbox.delivered": InboxDeliveredData,
    "session.inbox.cancelled": InboxDeliveredData,
    "session.inbox.delivery.changed": InboxDeliveryChangedData,
    "session.step.started": StepStartedData,
    "session.step.streamed": StepStreamedData,
    "session.step.ended": StepEndedData,
    "session.step.failed": StepFailedData,
    "session.text.started": TextStartedData,
    "session.text.delta": TextDeltaData,
    "session.text.ended": TextEndedData,
    "session.reasoning.started": TextStartedData,
    "session.reasoning.delta": TextDeltaData,
    "session.reasoning.ended": TextEndedData,
    "session.tool.input.started": ToolInputStartedData,
    "session.tool.input.delta": ToolInputDeltaData,
    "session.tool.input.ended": ToolInputEndedData,
    "session.tool.called": ToolCalledData,
    "session.tool.progress": ToolProgressData,
    "session.tool.success": ToolSuccessData,
    "session.tool.failed": ToolFailedData,
    "session.compaction.started": CompactionStartedData,
    "session.compaction.delta": CompactionDeltaData,
    "session.compaction.ended": CompactionEndedData,
    "session.compaction.failed": CompactionFailedData,
    "session.message.content.updated": MessageContentUpdatedData,
    "session.usage.updated": UsageUpdatedData,
    "session.usage.recorded": UsageRecordedData,
    "session.synthetic": SyntheticData,
    "session.skill.activated": SkillActivatedData,
    "session.shell.started": ShellStartedData,
    "session.shell.ended": ShellEndedData,
    "session.revert.staged": RevertStagedData,
    "session.revert.committed": RevertCommittedData,
    "session.revert.cleared": SessionRefData,
    "session.model.selected": ModelSelectedData,
    "session.agent.selected": AgentSelectedData,
    "permission.asked": PermissionRequest,
    "permission.replied": PermissionReplied,
    "form.created": FormInfo,
    "form.replied": FormRepliedData,
    "form.cancelled": FormRefData,
}


DURABLE_EVENT_TYPES = frozenset(
    {
        "session.created",
        "session.renamed",
        "session.archived",
        "session.unarchived",
        "session.moved",
        "session.deleted",
        "session.forked",
        "session.viewed",
        "session.execution.started",
        "session.execution.succeeded",
        "session.execution.failed",
        "session.execution.interrupted",
        "session.retry.scheduled",
        "session.inbox.enqueued",
        "session.inbox.delivered",
        "session.inbox.cancelled",
        "session.inbox.delivery.changed",
        "session.step.started",
        "session.step.streamed",
        "session.step.ended",
        "session.step.failed",
        "session.text.started",
        "session.text.ended",
        "session.reasoning.started",
        "session.reasoning.ended",
        "session.tool.input.started",
        "session.tool.input.ended",
        "session.tool.called",
        "session.tool.success",
        "session.tool.failed",
        "session.compaction.started",
        "session.compaction.ended",
        "session.compaction.failed",
        "session.message.content.updated",
        "session.usage.recorded",
        "session.synthetic",
        "session.skill.activated",
        "session.shell.started",
        "session.shell.ended",
        "session.revert.staged",
        "session.revert.committed",
        "session.revert.cleared",
        "session.model.selected",
        "session.agent.selected",
    }
)

VOLATILE_EVENT_TYPES = frozenset(
    {
        "server.connected",
        "session.status",
        "session.idle",
        "session.updated",
        "session.text.delta",
        "session.reasoning.delta",
        "session.tool.input.delta",
        "session.tool.progress",
        "session.compaction.delta",
        "session.usage.updated",
        "permission.asked",
        "permission.replied",
        "form.created",
        "form.replied",
        "form.cancelled",
    }
)


class EventEnvelope(StrictModel):
    id: str
    created: int
    type: EventType
    location: LocationRef | None = None
    durable: DurableRef | None = None
    data: dict[str, Any]

    @model_validator(mode="after")
    def _validate_payload(self) -> "EventEnvelope":
        model = EVENT_DATA_MODELS.get(self.type)
        if model is None:
            raise ValueError(f"unknown event type: {self.type}")
        model.model_validate(self.data)
        if self.type in DURABLE_EVENT_TYPES and self.durable is None:
            raise ValueError(f"durable event missing durable ref: {self.type}")
        if self.type in VOLATILE_EVENT_TYPES and self.durable is not None:
            raise ValueError(f"volatile event must not carry durable ref: {self.type}")
        return self


EVENT_ADAPTER = TypeAdapter(EventEnvelope)
EVENT_LIST_ADAPTER = TypeAdapter(list[EventEnvelope])

__all__ = [
    "AgentAttachment",
    "AgentSelectedData",
    "AssistantMessage",
    "CompactionEndedData",
    "CompactionFailedData",
    "CompactionMessage",
    "CompactionStartedData",
    "DURABLE_EVENT_TYPES",
    "Delivery",
    "DurableRef",
    "EVENT_ADAPTER",
    "EVENT_DATA_MODELS",
    "EVENT_LIST_ADAPTER",
    "EventEnvelope",
    "EventType",
    "ExecutionFailedData",
    "ExecutionInterruptedData",
    "FileAttachment",
    "FormField",
    "FormInfo",
    "FormRefData",
    "FormRepliedData",
    "InboxInfo",
    "InboxItem",
    "InboxUserPayload",
    "LocationRef",
    "MESSAGE_ADAPTER",
    "MESSAGE_LIST_ADAPTER",
    "MESSAGE_PART_LIST_ADAPTER",
    "MessageContentUpdatedData",
    "MessagePart",
    "MessageV2",
    "ModelRef",
    "PermissionReply",
    "PermissionRequest",
    "PermissionReplied",
    "PromptInput",
    "ReasoningPart",
    "RevertState",
    "SessionInfoV2",
    "SessionOutcome",
    "SessionState",
    "SessionStatusState",
    "SessionTime",
    "ShellOutput",
    "StrictModel",
    "StructuredError",
    "TextPart",
    "TokenUsage",
    "ToolContent",
    "ToolPart",
    "ToolState",
    "UsageRecordedData",
    "UsageUpdatedData",
    "VOLATILE_EVENT_TYPES",
    "VoiceAttachment",
]
