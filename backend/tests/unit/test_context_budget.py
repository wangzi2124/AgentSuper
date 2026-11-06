from __future__ import annotations

from cognition.context_budget import (
    ContextBudget,
    MIN_GAIN_TOKENS,
    MIN_PRESERVE_TOKENS,
    MAX_PRESERVE_TOKENS,
    min_gain_budget,
    normalized_output,
    preserve_recent_budget,
    prune_gate_for,
    transport_split,
)


def test_normalized_output_uses_explicit_max_output():
    assert normalized_output(128_000, 8_000) == 8_000


def test_normalized_output_rejects_max_output_equal_to_window():
    assert normalized_output(8_192, 8_192) == 2_048
    assert normalized_output(4_096, 0) == 2_048


def test_preserve_recent_is_clamped():
    assert preserve_recent_budget(0) == MIN_PRESERVE_TOKENS
    assert preserve_recent_budget(20_000) == 5_000
    assert preserve_recent_budget(1_000_000) == MAX_PRESERVE_TOKENS


def test_min_gain_has_floor():
    assert min_gain_budget(0) == MIN_GAIN_TOKENS
    assert min_gain_budget(100_000) == 5_000


def test_prune_gate_is_seventy_percent():
    assert prune_gate_for(10_000) == 7_000


def test_transport_split_matches_guard_formula():
    assert transport_split(4_096) == (1_024, 327, 2_745)
    assert transport_split(8_192) == (2_048, 655, 5_489)
    assert transport_split(0) == (512, 256, 0)


def test_context_budget_plan_uses_normalized_output_reserve():
    budget = ContextBudget.plan(100_000, max_output_tokens=2_000)

    assert budget.output_reserve == 2_000
    assert budget.usable == 98_000
    assert budget.preserve_recent == 8_000
    assert budget.min_gain == 4_900
    assert budget.prune_gate == 68_600
    assert budget.transport_reserve == 25_000
    assert budget.transport_slack == 8_000
    assert budget.prompt_budget == 67_000


def test_context_budget_plan_honors_explicit_reserved():
    budget = ContextBudget.plan(100_000, max_output_tokens=2_000, reserved=64_000)

    assert budget.output_reserve == 64_000
    assert budget.usable == 36_000


def test_context_budget_plan_zero_window():
    budget = ContextBudget.plan(0)

    assert budget.usable == 0
    assert budget.output_reserve == 0
    assert budget.prompt_budget == 0


def test_guard_split_budget_delegates_to_transport_split():
    from models.context_guard import split_budget

    for window in (0, 4_096, 8_192, 32_768):
        reserve, slack, prompt = transport_split(window)
        assert split_budget(window) == (reserve, prompt)
