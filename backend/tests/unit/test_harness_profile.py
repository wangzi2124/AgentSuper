from __future__ import annotations

import pytest

from harness.capabilities import build_profile, clear_profile_cache


class _Model:
    name = "profile-model"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        raise AssertionError


def test_profile_uses_catalog_capabilities_and_limits(monkeypatch):
    clear_profile_cache()
    monkeypatch.setattr(
        "models.catalog.read_capabilities",
        lambda _mid: {"tool_use": True, "vision": True, "reasoning": False},
    )
    monkeypatch.setattr(
        "models.catalog.read_limits",
        lambda _mid: {"context_length": 128000, "max_output_tokens": 4096},
    )
    profile = build_profile("profile-model", force=True)
    assert profile.tool_mode == "native"
    assert profile.context_length == 128000
    assert profile.max_output_tokens == 4096
    assert profile.vision is True
    assert profile.source == "catalog"


def test_profile_env_override_and_cache(monkeypatch):
    clear_profile_cache()
    monkeypatch.setenv("HARNESS_FALLBACK_MODELS", "profile-model")
    monkeypatch.setattr(
        "models.catalog.read_capabilities",
        lambda _mid: {"tool_use": True, "vision": False, "reasoning": False},
    )
    monkeypatch.setattr(
        "models.catalog.read_limits",
        lambda _mid: {"context_length": 1000, "max_output_tokens": 100},
    )
    profile = build_profile("profile-model", force=True)
    assert profile.tool_mode == "fallback"
    assert profile.source == "env"

    monkeypatch.setattr(
        "models.catalog.read_capabilities",
        lambda _mid: {"tool_use": False, "vision": False, "reasoning": False},
    )
    cached = build_profile("profile-model")
    assert cached.tool_mode == "fallback"
    refreshed = build_profile("profile-model", force=True)
    assert refreshed.tool_mode == "fallback"


def test_tool_adapter_exposes_profile(monkeypatch):
    from harness.tool_adapter import ToolAdapter

    clear_profile_cache()
    monkeypatch.setenv("HARNESS_FALLBACK_MODELS", "profile-model")
    adapter = ToolAdapter(_Model())
    assert adapter.profile.model_id == "profile-model"
    assert adapter.mode == "fallback"
