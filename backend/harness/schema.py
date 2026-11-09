"""Bounded, cached tool schema building (design §6.4)."""

from __future__ import annotations

import weakref
from typing import Any, Callable, Iterable

# 20：内置联网（web_search/web_fetch/http_request）与天气（get_weather）
# 常驻工具表后，需保留足够的 global 槽位给 task + load_skill_*，避免技能被挤掉。
MAX_TOOL_DEFS = 20

# Core tools that must never be crowded out by plugin/custom/MCP tools.
CORE_SCOPED_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "append_file",
        "delete_file",
        "create_directory",
        "ls",
        "grep",
        "glob",
        "shell_exec",
        "apply_patch",
    }
)

# Essential global tools: real-time/network capabilities the prompt tells the
# model to use. If too many scoped (plugin/custom) tools exist, these used to
# lose every slot, so the model was told to call `get_weather` while the tool
# was absent from its schema — it then hallucinated a result.
ESSENTIAL_GLOBAL_TOOLS = frozenset(
    {
        "web_search",
        "web_fetch",
        "http_request",
        "get_weather",
        "ask_question",
        "task",
    }
)


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

        Priority (highest first): core file/shell tools → essential global
        tools (web/weather/task/question) → other scoped tools (plugins, MCP,
        custom) → other global tools (skills). Essential globals are reserved
        so a large plugin library can no longer remove `get_weather`/`web_*`
        from the schema while the prompt still instructs the model to call it.
        """
        if len(definitions) <= self.limit:
            return definitions

        core = [td for td in definitions if getattr(td, "name", "") in CORE_SCOPED_TOOLS]
        essential = [
            td for td in definitions if getattr(td, "name", "") in ESSENTIAL_GLOBAL_TOOLS
        ]
        scoped_rest = [
            td
            for td in definitions
            if getattr(td, "scope", "") != "global"
            and getattr(td, "name", "") not in CORE_SCOPED_TOOLS
            and getattr(td, "name", "") not in ESSENTIAL_GLOBAL_TOOLS
        ]
        global_rest = [
            td
            for td in definitions
            if getattr(td, "scope", "") == "global"
            and getattr(td, "name", "") not in ESSENTIAL_GLOBAL_TOOLS
        ]

        selected = core + essential
        remaining = self.limit - len(selected)
        if remaining > 0:
            selected.extend(scoped_rest[:remaining])
            remaining = self.limit - len(selected)
        if remaining > 0:
            selected.extend(global_rest[:remaining])
        return selected[: self.limit]

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
        # Provider prefix caching keys on the exact tool list bytes. Sort after
        # selection so registration order / plugin load order cannot reshuffle
        # the prefix between processes while business tools still win slots.
        built.sort(
            key=lambda item: str((item.get("function") or {}).get("name") or "")
        )
        per_registry[scope] = (signature, built)
        return [dict(d) for d in built]


__all__ = ["MAX_TOOL_DEFS", "ToolSchemaCache"]
