"""Session/turn cost ledger (Harness governance).

Costs are monotonically increasing and survive model switches: the ledger is
created from the cumulative ``token_usage.cost`` already in graph state. A
``total <= 0`` means "no budget configured", so default behavior is unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class BudgetExceeded(RuntimeError):
    """Raised when a model call would exceed the configured session budget."""


@dataclass
class BudgetLedger:
    total: float = 0.0
    used: float = 0.0

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "BudgetLedger":
        usage = state.get("token_usage") or {}
        return cls(
            total=float(state.get("budget_total") or 0.0),
            used=float(usage.get("cost") or 0.0),
        )

    @property
    def enabled(self) -> bool:
        return self.total > 0

    def remaining(self) -> float:
        if not self.enabled:
            return math.inf
        return max(0.0, self.total - self.used)

    def exhausted(self) -> bool:
        return self.enabled and self.used >= self.total

    def ensure_available(self) -> None:
        if self.exhausted():
            raise BudgetExceeded(
                f"会话预算已用尽（used={self.used:.6f}, total={self.total:.6f}）"
            )

    def charge(self, cost: float) -> None:
        value = float(cost or 0.0)
        if value > 0:
            self.used += value

    def snapshot(self) -> dict[str, float]:
        return {"total": self.total, "used": self.used, "remaining": self.remaining()}


__all__ = ["BudgetExceeded", "BudgetLedger"]
