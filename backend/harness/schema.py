"""Bounded, cached tool schema building (design §6.4)."""

from __future__ import annotations

import weakref
from typing import Any, Callable, Iterable

# 15 → 20：内置联网（web_search/web_fetch/http_request）与天气（get_weather）
# 常驻工具表后，需保留足够的 global 槽位给 task + load_skill_*，避免技能被挤掉。
MAX_TOOL_DEFS = 20


def _signature(definitions: Iterable[Any]) -> tuple[Any, ...]:
    return tuple(
        (
            td.name,
            id(td.fn),
            td.description,
            td.scope,
            repr(td.parameters),
        )
        for td in definitions
    )


class ToolSchemaCache:
    """Weak registry-keyed cache; invalidates when definitions change."""

    def __init__(self, limit: int = MAX_TOOL_DEFS) -> None:
        self.limit = max(1, int(limit))
        self._cache: "weakref.WeakKeyDictionary[Any, dict[str | None, tuple[tuple[Any, ...], list[dict[str, Any]]]]]" = (
            weakref.WeakKeyDictionary()
        )

    def _select(self, definitions: list[Any]) -> list[Any]:
        """Select at most ``limit`` definitions for the model.

        Business tools (``scope != "global"``: file/shell/etc.) take priority;
        global tools (``task`` + one ``load_skill_*`` per enabled skill) fill the
        remaining slots. Previously globals came first, so a large skill library
        could crowd out ``write_file``/``shell_exec`` entirely — the model then
        could not act at all (no tool call → no permission prompt).
        """
        if len(definitions) <= self.limit:
            return definitions
        scoped = [td for td in definitions if getattr(td, "scope", "") != "global"]
        globals_ = [td for td in definitions if getattr(td, "scope", "") == "global"]
        return (scoped + globals_)[: self.limit]

    def build(
        self,
        registry: Any,
        scope: str | None,
        builder: Callable[[Any], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        definitions = list(registry.definitions(scope))
        signature = _signature(definitions)
        per_registry = self._cache.setdefault(registry, {})
        cached = per_registry.get(scope)
        if cached and cached[0] == signature:
            return [dict(d) for d in cached[1]]

        selected = self._select(definitions)
        built = [builder(td) for td in selected]
        per_registry[scope] = (signature, built)
        return [dict(d) for d in built]


__all__ = ["MAX_TOOL_DEFS", "ToolSchemaCache"]
