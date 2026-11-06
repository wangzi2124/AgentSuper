"""Stable Harness data contracts shared by kernel, loop and observability."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TurnContext:
    turn_id: str
    session_id: str
    user_id: str = "anonymous"
    model: str | None = None
    directory: str | None = None
    started_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    rounds: int = 0
    finish_reason: str = "stop"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelProfileSnapshot:
    """Capability snapshot used for one request (catalog + overrides)."""

    model_id: str
    tool_mode: str
    context_length: int = 0
    max_output_tokens: int = 0
    vision: bool = False
    reasoning: bool = False
    source: str = "default"


PART_LIFECYCLE = ("pending", "running", "completed", "failed")
_PART_EVENT_TYPES = ("tool_start", "tool_end", "step_start", "step_end")


def _part_status(event_type: str, value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in ("failed", "error"):
        return "failed"
    if status in PART_LIFECYCLE:
        return status
    return "running" if event_type.endswith("start") else "completed"


@dataclass(frozen=True)
class ToolPartProjection:
    """Canonical shape of one Tool Part crossing the Harness seam.

    Both the live SSE adapter and the persisted replay adapter consume this
    projection.  Lifecycle is explicit here; adapters must not re-derive it.
    """

    type: str
    part_id: str
    step_id: str
    agent_id: str
    agent_name: str
    name: str
    status: str
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    duration_ms: float | None = None
    error: str | None = None

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> ToolPartProjection | None:
        event_type = str(event.get("type") or "")
        if event_type not in _PART_EVENT_TYPES:
            return None
        step_id = str(event.get("step_id") or event.get("part_id") or "")
        part_id = str(event.get("part_id") or step_id)
        if not step_id:
            return None
        name = str(event.get("tool_name") or event.get("name") or "")
        args = event.get("tool_args")
        result = event.get("tool_result")
        duration = event.get("duration_ms")
        return cls(
            type=event_type,
            part_id=part_id,
            step_id=step_id,
            agent_id=str(event.get("agent_id") or "build"),
            agent_name=str(event.get("agent_name") or event.get("agent_id") or "build"),
            name=name,
            status=_part_status(event_type, event.get("status")),
            tool_args=dict(args) if isinstance(args, dict) else None,
            tool_result=None if result is None else str(result),
            duration_ms=float(duration) if isinstance(duration, (int, float)) else None,
            error=str(event["error"]) if event.get("error") else None,
        )

    def to_step(self) -> dict[str, Any]:
        step: dict[str, Any] = {
            "type": self.type,
            "step_id": self.step_id,
            "name": self.name,
            "status": self.status,
            "agent_id": self.agent_id,
            "part_id": self.part_id,
        }
        if self.name:
            step["tool_name"] = self.name
        if self.tool_args is not None:
            step["tool_args"] = dict(self.tool_args)
        if self.tool_result is not None:
            step["tool_result"] = self.tool_result
        if self.duration_ms is not None:
            step["duration_ms"] = self.duration_ms
        return step

    def to_event(self) -> dict[str, Any]:
        return self.to_step()


@dataclass(frozen=True)
class EventEnvelope:
    type: str
    turn_id: str
    session_id: str
    agent_id: str = "build"
    step_id: str | None = None
    seq: int = 0
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "step_id": self.step_id,
            "seq": self.seq,
            "ts": self.ts,
            "data": self.data,
        }


__all__ = [
    "EventEnvelope",
    "ModelProfileSnapshot",
    "PART_LIFECYCLE",
    "ToolPartProjection",
    "TurnContext",
    "TurnResult",
]
