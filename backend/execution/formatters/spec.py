"""Formatter specs (opencode gap G-03)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FormatterSpec:
    name: str
    command: tuple[str, ...]
    extensions: tuple[str, ...]
    environment: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    requires: tuple[str, ...] = ()
    timeout: float = 30.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FormatterSpec | None":
        name = str(raw.get("name") or "").strip()
        command = raw.get("command") or ()
        if isinstance(command, str):
            command = [command]
        if not name or not command:
            return None
        extensions = raw.get("extensions") or ()
        if isinstance(extensions, str):
            extensions = [extensions]
        requires = raw.get("requires") or ()
        if isinstance(requires, str):
            requires = [requires]
        env = raw.get("environment") or {}
        try:
            timeout = float(raw.get("timeout") or 30.0)
        except (TypeError, ValueError):
            timeout = 30.0
        return cls(
            name=name,
            command=tuple(str(part) for part in command),
            extensions=tuple(str(ext) for ext in extensions),
            environment={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            disabled=bool(raw.get("disabled") or False),
            requires=tuple(str(item) for item in requires),
            timeout=max(1.0, timeout),
        )


@dataclass(frozen=True)
class FormatterResult:
    status: str
    formatter: str = ""
    duration: float = 0.0
    output: str = ""
    path: str = ""


__all__ = ["FormatterResult", "FormatterSpec"]
