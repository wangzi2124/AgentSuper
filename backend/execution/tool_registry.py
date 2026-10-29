"""工具注册表（对齐 agent.md §5.3）。

统一管理工具的作用域、权限与配额。scope ∈ {global, subagent, location}；
require_approval 表示高危操作需人工审批（已接入审批流）。
token_budget / rate_limit 由 ToolRegistryQuota 在工具执行入口强制执行（0 = 不限）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class ToolDefinition:
    name: str
    description: str
    fn: Callable[..., Any]
    scope: str = "global"
    require_approval: bool = False
    # 配额（0 = 不限制）；显式声明后才由 ToolRegistryQuota 执行。
    token_budget: int = 0
    rate_limit: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """工具注册表：按 scope 下发、按 name 查询审批要求。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        # 由用户「自定义工具」（plugins/custom_*.py）注册的工具名；
        # toggle/删除后只清理这一部分，绝不动内置工具。
        self._plugin_tools: set[str] = set()

    def register(self, td: ToolDefinition) -> None:
        if td.name in self._tools:
            raise ValueError(f"工具重名: {td.name}")
        self._tools[td.name] = td

    def register_function(
        self,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> ToolDefinition:
        td = ToolDefinition(
            name=name or getattr(fn, "__name__", "tool"),
            description=description or (fn.__doc__ or "").strip(),
            fn=fn,
            **kwargs,
        )
        self.register(td)
        return td

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def unregister(self, name: str) -> None:
        """移除一个工具（仅用于插件/自定义工具的重新同步）。"""
        self._tools.pop(name, None)
        self._plugin_tools.discard(name)

    def register_plugin_function(
        self,
        fn: Callable[..., Any],
        *,
        name: str,
        description: str = "",
        scope: str = "location",
        require_approval: bool = True,
    ) -> bool:
        """注册插件（自定义工具）函数；与内置工具重名时保留内置并返回 False。"""
        if name in self._tools:
            return False
        self.register_function(
            fn,
            name=name,
            description=description,
            scope=scope,
            require_approval=require_approval,
        )
        self._plugin_tools.add(name)
        return True

    def clear_plugin_tools(self) -> None:
        """清空上一次同步进来的插件工具（不动内置工具）。"""
        for name in list(self._plugin_tools):
            self._tools.pop(name, None)
        self._plugin_tools.clear()

    def plugin_tool_names(self) -> list[str]:
        return list(self._plugin_tools)

    def subset(self, names: "set[str] | list[str] | tuple[str, ...]") -> "ToolRegistry":
        """返回仅含指定工具名的新注册表（用于子 Agent 工具白名单，§5.4）。"""
        wanted = set(names)
        new = ToolRegistry()
        for name, td in self._tools.items():
            if name in wanted:
                new._tools[name] = td
        return new

    def get_tools(self, scope: str | None = None) -> list[Callable[..., Any]]:
        return [
            td.fn
            for td in self._tools.values()
            if scope is None or td.scope in (scope, "global")
        ]

    def definitions(self, scope: str | None = None) -> list[ToolDefinition]:
        return [
            td
            for td in self._tools.values()
            if scope is None or td.scope in (scope, "global")
        ]

    def needs_approval(self, name: str) -> bool:
        return self._tools[name].require_approval if name in self._tools else True

    def names(self) -> list[str]:
        return list(self._tools.keys())


class SessionToolCache:
    """会话级「已挂载工具名」缓存（对齐 DeepSeek 前缀缓存语义）。

    同一会话内已挂载工具名**只增不减**：首命中按注册序播种，后续命中仅在**尾部追加**
    尚未挂载的名字 —— 使 [system + tools] 前缀跨请求字节稳定，命中 prompt 前缀缓存
    （0.1x 计费 + TTFT 下降）。会话数超 `max_sessions` 时按插入序淘汰最旧（LRU 简化版）。

    `resolve` 在 `key` 为空时即时筛选且**不写缓存**；`add_used` 把本请求实际调用的
    工具名并入（模型可能调用未挂载工具，下一轮自动带上）。
    """

    def __init__(self, max_sessions: int = 200) -> None:
        self._mounted: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._max = max(1, int(max_sessions))

    def resolve(
        self, key: str, wanted: "set[str]", registered: Sequence[str]
    ) -> list[str]:
        """返回本轮应挂载的工具名（注册序，会话内只增不减）。"""
        if not key:
            return [n for n in registered if n in wanted]
        with self._lock:
            mounted = self._mounted.get(key)
            if mounted is None:
                mounted = [n for n in registered if n in wanted]
                self._mounted[key] = mounted
                self._evict(key)
            else:
                self._append_new(mounted, wanted, registered)
            return list(mounted)

    def add_used(
        self, key: str, used: "set[str]", registered: Sequence[str]
    ) -> None:
        """把本请求实际用到的工具名并入会话缓存（仅尾部追加）。"""
        if not key or not used:
            return
        with self._lock:
            mounted = self._mounted.get(key)
            if mounted is None:
                self._mounted[key] = [n for n in registered if n in used]
                return
            self._append_new(mounted, used, registered)
            self._evict(key)

    @staticmethod
    def _append_new(
        mounted: list[str], wanted: "set[str]", registered: Sequence[str]
    ) -> None:
        existing = set(mounted)
        for n in registered:
            if n not in existing and n in wanted:
                mounted.append(n)
                existing.add(n)

    def _evict(self, current: str) -> None:
        if len(self._mounted) > self._max:
            oldest = next(iter(self._mounted))
            if oldest != current:
                del self._mounted[oldest]
