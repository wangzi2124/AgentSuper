"""生产加固：ToolRegistry 配额（速率限制、token_budget）。

已接线：`graph/nodes/base.py:_execute_tool_call` 在每次工具执行前调用本模块；
只有显式声明了 `rate_limit` / `token_budget` 的工具才受限（默认 0 = 不限）。

对齐 agent.md §5.3 与 M4「生产加固」：按工具维度限制 **每分钟调用次数** 与
**累计 token 预算**，超限即拒绝，避免单个工具被滥用拖垮成本/稳定性。

- `allow(name, cost_tokens)`：原子检查 + 记账（通过则消耗一次调用与 token）。
- `consume(name, cost_tokens)`：仅记账（用于事后补记真实用量）。
- `reset(name)`：清空某工具（或全部）的计数，便于窗口滚动/测试。
- `get_quota(registry)`：按 Registry 实例复用配额状态（跨轮次累计）。
"""

from __future__ import annotations

import threading
import time
import weakref
from collections import defaultdict, deque
from dataclasses import dataclass, field

RATE_WINDOW_SEC = 60.0


@dataclass(frozen=True)
class QuotaLimits:
    """工具实际生效的配额限制（0 = 不限）。"""

    rate_limit: int = 0
    token_budget: int = 0


@dataclass
class RateLimitState:
    """单个工具的滑动窗口状态。"""

    timestamps: deque[float] = field(default_factory=deque)
    tokens_used: int = 0


class ToolRegistryQuota:
    """基于 `ToolRegistry` 中 ToolDefinition 的 rate_limit / token_budget 做配额。"""

    def __init__(
        self,
        registry,
        *,
        default_rate_limit: int = 0,
        default_token_budget: int = 0,
    ):
        self.registry = registry
        self.default_rate_limit = max(0, int(default_rate_limit or 0))
        self.default_token_budget = max(0, int(default_token_budget or 0))
        self._state: dict[str, RateLimitState] = defaultdict(RateLimitState)
        self._lock = threading.Lock()

    def _effective(self, td) -> QuotaLimits:
        """工具显式限制优先；0/未设置时回落到全局默认（0 = 不限）。"""
        rate = int(getattr(td, "rate_limit", 0) or 0) or self.default_rate_limit
        budget = int(getattr(td, "token_budget", 0) or 0) or self.default_token_budget
        return QuotaLimits(max(0, rate), max(0, budget))

    def limits(self, name: str) -> QuotaLimits:
        """返回工具实际生效的配额。"""
        td = self.registry.get(name)
        return self._effective(td) if td else QuotaLimits()

    def _prune(self, state: RateLimitState, now: float) -> None:
        while state.timestamps and now - state.timestamps[0] > RATE_WINDOW_SEC:
            state.timestamps.popleft()

    def allow(self, name: str, cost_tokens: int = 0) -> bool:
        """检查并（通过时）记账一次调用。超限返回 False，不消耗配额。"""
        td = self.registry.get(name)
        if not td:
            return False
        limits = self._effective(td)
        now = time.time()
        cost = max(0, int(cost_tokens))
        with self._lock:
            state = self._state[name]
            if limits.rate_limit > 0:
                self._prune(state, now)
                if len(state.timestamps) >= limits.rate_limit:
                    return False
            if limits.token_budget > 0 and state.tokens_used + cost > limits.token_budget:
                return False
            if limits.rate_limit > 0:
                state.timestamps.append(now)
            state.tokens_used += cost
            return True

    def consume(self, name: str, cost_tokens: int = 0) -> bool:
        """仅补记 token 用量（不改动速率窗口），返回是否仍在预算内。"""
        td = self.registry.get(name)
        if not td:
            return False
        limits = self._effective(td)
        cost = max(0, int(cost_tokens))
        with self._lock:
            state = self._state[name]
            state.tokens_used += cost
            if limits.token_budget > 0 and state.tokens_used > limits.token_budget:
                return False
            return True

    def adjust(self, name: str, delta_tokens: int) -> bool:
        """结算预留与实际的差额（可为负）；返回结算后是否仍在预算内。"""
        td = self.registry.get(name)
        if not td:
            return False
        limits = self._effective(td)
        delta = int(delta_tokens)
        with self._lock:
            state = self._state[name]
            state.tokens_used = max(0, state.tokens_used + delta)
            if limits.token_budget > 0 and state.tokens_used > limits.token_budget:
                return False
            return True

    def used_tokens(self, name: str) -> int:
        with self._lock:
            return self._state[name].tokens_used

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name is None:
                self._state.clear()
            else:
                self._state.pop(name, None)


_instances: "weakref.WeakKeyDictionary[object, ToolRegistryQuota]" = weakref.WeakKeyDictionary()
_instances_lock = threading.Lock()


def get_quota(registry: object) -> ToolRegistryQuota:
    """按 ToolRegistry 实例复用配额状态（跨轮次累计；Registry 回收后自动清理）。"""
    with _instances_lock:
        quota = _instances.get(registry)
        if quota is None:
            try:
                from config import settings

                default_rate = int(getattr(settings, "tool_default_rate_limit", 0) or 0)
                default_budget = int(getattr(settings, "tool_default_token_budget", 0) or 0)
            except Exception:  # noqa: BLE001 - 配置不可用时保持不限
                default_rate = default_budget = 0
            quota = ToolRegistryQuota(
                registry,
                default_rate_limit=default_rate,
                default_token_budget=default_budget,
            )
            _instances[registry] = quota
        return quota


__all__ = ["RATE_WINDOW_SEC", "QuotaLimits", "RateLimitState", "ToolRegistryQuota", "get_quota"]
