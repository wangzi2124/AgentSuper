"""Per-turn command overrides (opencode gap G-02)."""

from __future__ import annotations

import contextvars
from typing import Any

_OVERRIDE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "command_override", default={}
)


def set_command_override(
    agent: str = "",
    model: str = "",
    *,
    subtask: bool = False,
    command: str = "",
) -> None:
    value: dict[str, Any] = {"agent": str(agent or ""), "model": str(model or "")}
    if subtask:
        value["subtask"] = True
    if command:
        value["command"] = str(command)
    _OVERRIDE.set(value)


def consume_command_override() -> dict[str, Any]:
    value = dict(_OVERRIDE.get() or {})
    _OVERRIDE.set({})
    return value


__all__ = ["consume_command_override", "set_command_override"]
