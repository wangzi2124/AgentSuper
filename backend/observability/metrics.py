"""Token / 成本计量（对齐 agent.md §11.3）。

进程内累计 + 按 tier/model 归因，并复用应用既有 monitor（若可用）做持久化。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .cost import estimate_cost

_lock = threading.Lock()


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


@dataclass
class MetricsRecorder:
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    by_model: dict[str, ModelUsage] = field(default_factory=dict)

    def record(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float | None = None,
    ) -> ModelUsage:
        if cost is None:
            cost = estimate_cost(model, input_tokens, output_tokens)
        with _lock:
            self.total_calls += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_cost += cost
            usage = self.by_model.setdefault(model, ModelUsage())
            usage.calls += 1
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.cost += cost
            return usage

    def snapshot(self) -> dict[str, Any]:
        with _lock:
            return {
                "total_calls": self.total_calls,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cost": round(self.total_cost, 6),
                "by_model": {
                    m: {
                        "calls": u.calls,
                        "input_tokens": u.input_tokens,
                        "output_tokens": u.output_tokens,
                        "cost": round(u.cost, 6),
                    }
                    for m, u in self.by_model.items()
                },
            }


_recorder = MetricsRecorder()


def get_recorder() -> MetricsRecorder:
    return _recorder


def record_llm_call(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float | None = None,
    duration_ms: float = 0.0,
    tool_rounds: int = 0,
    tool_calls: int = 0,
    reasoning_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
) -> ModelUsage:
    if cost is None:
        cost = estimate_cost(model, input_tokens, output_tokens)
    usage = _recorder.record(model, input_tokens, output_tokens, cost)
    try:
        from . import monitor

        monitor.record_model_call(
            model=model,
            prompt_tokens=int(input_tokens or 0),
            completion_tokens=int(output_tokens or 0),
            duration_ms=float(duration_ms or 0),
            tool_rounds=int(tool_rounds or 0),
            tool_calls=int(tool_calls or 0),
            reasoning_tokens=int(reasoning_tokens or 0),
            cache_read=int(cache_read or 0),
            cache_write=int(cache_write or 0),
            cost=float(cost or 0),
        )
    except Exception:
        pass
    return usage
