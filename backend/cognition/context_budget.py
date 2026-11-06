"""Single source of truth for context window budgets.

Consumers:

- ``harness.compression``: compaction gate, preserve-recent and min-gain;
- ``models.context_guard``: Ollama transport reserve/slack prompt budget;
- ``graph.nodes.base``: prune gate and overflow preserve budget.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPACTION_BUFFER = 20_000
MIN_USABLE_TOKENS = 2_048
MIN_PRESERVE_TOKENS = 2_000
MAX_PRESERVE_TOKENS = 8_000
PRESERVE_RATIO = 0.25
MIN_GAIN_TOKENS = 1_024
MIN_GAIN_RATIO = 0.05
PRUNE_GATE_RATIO = 0.7

TRANSPORT_RESERVE_RATIO = 0.25
TRANSPORT_RESERVE_MIN = 512
TRANSPORT_SLACK_RATIO = 0.08
TRANSPORT_SLACK_MIN = 256


def normalized_output(window: int, max_output_tokens: int) -> int:
    """Model output budget; rejects ``max_output == context`` catalog reports."""
    window = max(0, int(window or 0))
    output = max(0, int(max_output_tokens or 0))
    if 0 < output < window:
        return output
    return min(8_192, max(2_048, window // 4))


def preserve_recent_budget(usable: int) -> int:
    """opencode preserveRecentBudget: clamp(25% × usable, 2k, 8k)."""
    return min(
        MAX_PRESERVE_TOKENS,
        max(MIN_PRESERVE_TOKENS, int(max(0, usable) * PRESERVE_RATIO)),
    )


def min_gain_budget(usable: int) -> int:
    """Minimum saved tokens required to write a checkpoint."""
    return max(MIN_GAIN_TOKENS, int(max(0, usable) * MIN_GAIN_RATIO))


def prune_gate_for(usable: int) -> int:
    """Only prune after estimated usage crosses this share of the usable window."""
    return int(max(0, usable) * PRUNE_GATE_RATIO)


def transport_split(window: int) -> tuple[int, int, int]:
    """``(reserve, slack, prompt_budget)`` for the Ollama transport guard."""
    window = max(0, int(window or 0))
    reserve = max(TRANSPORT_RESERVE_MIN, int(window * TRANSPORT_RESERVE_RATIO))
    slack = max(TRANSPORT_SLACK_MIN, int(window * TRANSPORT_SLACK_RATIO))
    return reserve, slack, max(0, window - reserve - slack)


@dataclass(frozen=True)
class ContextBudget:
    window: int
    output_reserve: int
    usable: int
    preserve_recent: int
    min_gain: int
    prune_gate: int
    transport_reserve: int
    transport_slack: int
    prompt_budget: int

    @classmethod
    def plan(
        cls,
        window: int,
        *,
        max_output_tokens: int = 0,
        reserved: int = 0,
    ) -> "ContextBudget":
        window = max(0, int(window or 0))
        output = normalized_output(window, max_output_tokens)
        reserve = (
            int(reserved)
            if int(reserved or 0) > 0
            else (min(COMPACTION_BUFFER, output) if window > 0 else 0)
        )
        usable = max(MIN_USABLE_TOKENS, window - reserve) if window > 0 else 0
        transport_reserve, transport_slack, prompt_budget = transport_split(window)
        return cls(
            window=window,
            output_reserve=reserve,
            usable=usable,
            preserve_recent=preserve_recent_budget(usable),
            min_gain=min_gain_budget(usable),
            prune_gate=prune_gate_for(usable),
            transport_reserve=transport_reserve,
            transport_slack=transport_slack,
            prompt_budget=prompt_budget,
        )


__all__ = [
    "COMPACTION_BUFFER",
    "ContextBudget",
    "MIN_GAIN_RATIO",
    "MIN_GAIN_TOKENS",
    "MIN_PRESERVE_TOKENS",
    "MIN_USABLE_TOKENS",
    "MAX_PRESERVE_TOKENS",
    "PRUNE_GATE_RATIO",
    "min_gain_budget",
    "normalized_output",
    "preserve_recent_budget",
    "prune_gate_for",
    "transport_split",
]
