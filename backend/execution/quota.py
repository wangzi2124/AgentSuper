"""生产加固：ToolRegistry 配额（速率限制、token_budget）。

对齐 agent.md §5.3 与 M4「生产加固」：按工具维度限制**每分钟调用次数**与
**累计 token 预算**，超限即拒绝，避免单个工具被滥用拖垮成本/稳定性。

- `allow(name, cost_tokens)`：原子检查 + 记账（通过则消耗一次调用与 token）。
- `consume(name, cost_tokens)`：仅记账（用于事后补记真实用量）。
- `reset(name)`：清空某工具（或全部）的计数，便于窗口滚动/测试。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

RATE_WINDOW_SEC = 60.0


@dataclass
class RateLimitState:
    """单个工具的滑动窗口状态。"""

    timestamps: deque[float] = field(default_factory=deque)
    tokens_used: int = 0


class ToolRegistryQuota:
    """基于 `ToolRegistry` 中 ToolDefinition 的 rate_limit / token_budget 做配额。"""

    def __init__(self, registry):
        self.registry = registry
        self._state: dict[str, RateLimitState] = defaultdict(RateLimitState)
        self._lock = threading.Lock()

    def _prune(self, state: RateLimitState, now: float) -> None:
        while state.timestamps and now - state.timestamps[0] > RATE_WINDOW_SEC:
            state.timestamps.popleft()

    def allow(self, name: str, cost_tokens: int = 0) -> bool:
        """检查并（通过时）记账一次调用。超限返回 False，不消耗配额。"""
        td = self.registry.get(name)
        if not td:
            return False
        now = time.time()
        cost = max(0, int(cost_tokens))
        with self._lock:
            state = self._state[name]
            if td.rate_limit > 0:
                self._prune(state, now)
                if len(state.timestamps) >= td.rate_limit:
                    return False
            if td.token_budget > 0 and state.tokens_used + cost > td.token_budget:
                return False
            if td.rate_limit > 0:
                state.timestamps.append(now)
            state.tokens_used += cost
            return True

    def consume(self, name: str, cost_tokens: int = 0) -> bool:
        """仅补记 token 用量（不改动速率窗口），返回是否仍在预算内。"""
        td = self.registry.get(name)
        if not td:
            return False
        cost = max(0, int(cost_tokens))
        with self._lock:
            state = self._state[name]
            state.tokens_used += cost
            if td.token_budget > 0 and state.tokens_used > td.token_budget:
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
