"""Bounded, cached tool schema building (design §6.4)."""

from __future__ import annotations

import weakref
from typing import Any, Callable, Iterable

MAX_TOOL_DEFS = 15


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
        if len(definitions) <= self.limit:
            return definitions
        globals_ = [td for td in definitions if getattr(td, "scope", "") == "global"]
        scoped = [td for td in definitions if getattr(td, "scope", "") != "global"]
        return (globals_ + scoped)[: self.limit]

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
