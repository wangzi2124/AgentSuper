"""Per-turn command overrides (opencode gap G-02)."""

from __future__ import annotations

import contextvars
from typing import Any

_OVERRIDE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "command_override", default={}
)


def set_command_override(agent: str = "", model: str = "") -> None:
    _OVERRIDE.set({"agent": str(agent or ""), "model": str(model or "")})


def consume_command_override() -> dict[str, Any]:
    value = dict(_OVERRIDE.get() or {})
    _OVERRIDE.set({})
    return value


__all__ = ["consume_command_override", "set_command_override"]
