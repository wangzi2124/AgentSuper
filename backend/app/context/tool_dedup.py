"""Tool result deduplication module.

Detects and deduplicates repeated tool calls with identical arguments
within a single generation round. Prevents the LLM from wasting context
on identical tool results (e.g., reading the same file multiple times).

Strategy:
- Hash (tool_name, sorted_args) to create a dedup key.
- On cache hit, return the cached result instead of re-executing.
- Cache is scoped per _generate() call (created fresh each invocation).
"""

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _make_dedup_key(tool_name: str, args: dict) -> str:
    """Create a deterministic hash key from tool name and arguments.

    Sorts args keys to ensure (a=1, b=2) and (b=2, a=1) produce the same key.
    """
    try:
        args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        args_str = str(sorted(args.items()))
    raw = f"{tool_name}:{args_str}"
    return hashlib.md5(raw.encode()).hexdigest()


class ToolResultDedup:
    """Per-generation tool result deduplication cache.

    Usage:
        dedup = ToolResultDedup()
        for tool_call in tool_calls:
            key = dedup.make_key(tool_name, args)
            cached = dedup.get(key)
            if cached is not None:
                result = cached  # Skip execution
            else:
                result = await execute_tool(tool_name, args)
                dedup.set(key, result)

        # When done, optionally get stats
        stats = dedup.stats()
    """

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._hits = 0
        self._misses = 0

    def make_key(self, tool_name: str, args: dict) -> str:
        """Create a dedup key for a tool call."""
        return _make_dedup_key(tool_name, args)

    def get(self, key: str) -> str | None:
        """Look up a cached result. Returns None on miss."""
        if key in self._cache:
            self._hits += 1
            logger.debug("Dedup cache hit: %s", key[:8])
            return self._cache[key]
        self._misses += 1
        return None

    def set(self, key: str, result: str):
        """Store a tool result in the cache."""
        self._cache[key] = result

    def stats(self) -> dict[str, int]:
        """Return dedup statistics."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "cached_entries": len(self._cache),
            "total_calls": self._hits + self._misses,
        }

    def clear(self):
        """Clear the cache (call between generations if needed)."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
