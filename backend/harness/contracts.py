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
    "TurnContext",
    "TurnResult",
]
