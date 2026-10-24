"""Env-gated real-model smoke for the Harness tool path.

Disabled by default. Run manually with:
  HARNESS_SMOKE_MODEL_A=<native-model> \
  HARNESS_SMOKE_MODEL_B=<fallback-model> \
  pytest tests/integration/test_harness_tool_history_smoke.py -q

This intentionally stays at Adapter + Loop level: the deterministic integration
test covers switch/summary, while this smoke checks real-provider protocol
behavior without depending on session storage or a compression model.
"""

from __future__ import annotations

import os

import pytest

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import TierModel, reset_request_model, set_request_model

_MODEL_A = os.getenv("HARNESS_SMOKE_MODEL_A", "").strip()
_MODEL_B = os.getenv("HARNESS_SMOKE_MODEL_B", "").strip()

pytestmark = pytest.mark.skipif(
    not (_MODEL_A and _MODEL_B),
    reason="set HARNESS_SMOKE_MODEL_A / HARNESS_SMOKE_MODEL_B to run",
)

PROMPT = (
    "Call the tool_echo tool exactly once with text='ORION-42'. "
    "Then reply with the tool result. Do not make up a result."
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="tool_echo",
            description="Echo a text value back.",
            fn=lambda text="": f"echo:{text}",
        )
    )
    return registry


async def _run(model_id: str, *, mode: str):
    model = TierModel(tier="auto")
    token = set_request_model(model_id)
    try:
        adapter = ToolAdapter(model, mode=mode, model_id=model_id)
        return await run_tool_loop(
            model,
            [{"role": "user", "content": PROMPT}],
            _registry(),
            adapter=adapter,
            max_rounds=3,
        )
    finally:
        reset_request_model(token)


@pytest.mark.asyncio
async def test_real_native_model_tool_loop():
    out = await _run(_MODEL_A, mode="native")
    assert out["tool_trace"], "native model did not call the tool"
    assert "ORION-42" in out["text"]


@pytest.mark.asyncio
async def test_real_fallback_model_tool_loop():
    out = await _run(_MODEL_B, mode="fallback")
    assert out["tool_trace"], "fallback model did not emit a delimiter call"
    assert "ORION-42" in out["text"]
