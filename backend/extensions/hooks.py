"""Ordered plugin hook bus with exception isolation (opencode gap G-09)."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

HOOK_NAMES = (
    "tool.execute.before",
    "tool.execute.after",
    "chat.message",
    "permission.ask",
    "session.start",
    "session.end",
    "tool.register",
)


class HookBus:
    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[int, str, Callable[..., Any]]]] = {
            name: [] for name in HOOK_NAMES
        }
        self._disabled: set[str] = set()

    def set_enabled(self, owner: str, enabled: bool) -> None:
        if enabled:
            self._disabled.discard(owner)
        else:
            self._disabled.add(owner)

    def register(self, event: str, owner: str, fn: Callable[..., Any], priority: int = 100) -> None:
        if event not in self._hooks:
            raise ValueError(f"unknown hook event: {event}")
        self._hooks[event].append((int(priority), owner, fn))
        self._hooks[event].sort(key=lambda item: item[0])

    def unregister_owner(self, owner: str) -> int:
        removed = 0
        for event, items in self._hooks.items():
            keep = [item for item in items if item[1] != owner]
            removed += len(items) - len(keep)
            self._hooks[event] = keep
        return removed

    def clear(self) -> None:
        for event in self._hooks:
            self._hooks[event] = []

    def names(self, event: str) -> list[str]:
        return [owner for _priority, owner, _fn in self._hooks.get(event, [])]

    async def emit(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run hooks in order; a failing hook is logged and skipped."""
        current = payload
        for _priority, owner, fn in list(self._hooks.get(event, [])):
            if owner in self._disabled:
                continue
            try:
                result = fn(dict(current))
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, dict):
                    current = result
            except Exception as exc:  # noqa: BLE001
                logger.warning("plugin hook %s/%s failed: %s", event, owner, exc)
        return current


_BUS: HookBus | None = None


def get_hook_bus() -> HookBus:
    global _BUS
    if _BUS is None:
        _BUS = HookBus()
    return _BUS


__all__ = ["HOOK_NAMES", "HookBus", "get_hook_bus"]
