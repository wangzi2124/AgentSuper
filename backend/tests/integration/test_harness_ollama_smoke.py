"""Env-gated Ollama Harness smoke: native tools + text fallback."""

from __future__ import annotations

import json
import urllib.request

import pytest

from config import settings
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import run_tool_loop
from harness.tool_adapter import ToolAdapter
from models.router import TierModel, reset_request_model, set_request_model

_ENABLED = bool(settings.harness_ollama_test)
_TOOLS_MODEL = settings.harness_ollama_tools_model or "ollama/qwen2.5:3b"
_TEXT_MODEL = settings.harness_ollama_text_model or "ollama/deepseek-r1:latest"
_BASE = settings.ollama_host or "http://127.0.0.1:11434"


def _installed() -> set[str]:
    try:
        with urllib.request.urlopen(f"{_BASE}/api/tags", timeout=3) as resp:
            data = json.load(resp)
        return {str(item.get("name") or "") for item in data.get("models", [])}
    except Exception:  # noqa: BLE001
        return set()


def _model_name(model_id: str) -> str:
    return model_id.split("/", 1)[-1]


_INSTALLED = _installed() if _ENABLED else set()

pytestmark = pytest.mark.skipif(
    not _ENABLED
    or _model_name(_TOOLS_MODEL) not in _INSTALLED
    or _model_name(_TEXT_MODEL) not in _INSTALLED,
    reason="set HARNESS_OLLAMA_TEST=1 and install both Ollama models",
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Echo text back.",
            fn=lambda text="": f"ECHO:{text}",
        )
    )
    return registry


async def _run(model_id: str, mode: str):
    model = TierModel(tier="auto", timeout=300)
    token = set_request_model(model_id)
    try:
        return await run_tool_loop(
            model,
            [
                {
                    "role": "user",
                    "content": "Call echo once with text=ORION-42, then answer with the result.",
                }
            ],
            _registry(),
            adapter=ToolAdapter(model, mode=mode, model_id=model_id),
            max_rounds=3,
        )
    finally:
        reset_request_model(token)


@pytest.mark.asyncio
async def test_ollama_native_tools_path():
    out = await _run(_TOOLS_MODEL, "native")
    assert out["tool_trace"], out["text"]
    assert "ORION-42" in out["tool_trace"][0]["output"]


@pytest.mark.asyncio
async def test_ollama_text_fallback_path():
    out = await _run(_TEXT_MODEL, "fallback")
    assert out["tool_trace"], out["text"]
    assert "ORION-42" in out["tool_trace"][0]["output"]
