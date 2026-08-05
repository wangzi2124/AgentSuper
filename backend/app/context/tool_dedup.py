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


# 非确定性工具：输出随外部状态/时间/网络变化，同一轮内相同参数重复调用
# 也可能返回不同结果，缓存上一次结果会返回过期数据，因此永远跳过去重。
# 命名遵循插件注册格式 plugin_<PLUGIN_NAME>_<func_name>，与 filesystem 原生工具。
NON_IDEMPOTENT_TOOLS: frozenset[str] = frozenset({
    "tool_execute",
    "tool_get_current_time",
    "tool_http_request",
    "tool_http_get",
    "tool_http_post",
    "plugin_weather_tool_get_weather",
    "plugin_weather_tool_get_weather_summary",
    "plugin_weather-alert_tool_get_weather_alert",
    "plugin_weather-alert_tool_get_weather_summary",
    "plugin_weather-alert_tool_get_typhoon_info",
    "plugin_example-plugin_tool_get_current_time",
    "plugin_internet-search_tool_internet_search",
    "plugin_internet-search_tool_extract_urls",
    "plugin_http-client_tool_http_request",
    "plugin_http-client_tool_http_get",
    "plugin_http-client_tool_http_post",
})


class ToolResultDedup:
    """Per-generation tool result deduplication cache.

    Usage:
        dedup = ToolResultDedup()
        for tool_call in tool_calls:
            if not dedup.should_dedup(tool_name):
                result = await execute_tool(tool_name, args)  # 非确定性工具：始终执行
                continue
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

    def __init__(self, skip_names: set[str] | frozenset[str] | None = None):
        self._cache: dict[str, str] = {}
        self._hits = 0
        self._misses = 0
        self._skip_names = set(NON_IDEMPOTENT_TOOLS) | set(skip_names or ())

    def should_dedup(self, tool_name: str) -> bool:
        """非确定性工具（weather/时间/网络/执行等）不参与去重，避免返回过期结果。"""
        return tool_name not in self._skip_names

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
