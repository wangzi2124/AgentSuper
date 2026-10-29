from config import settings
from harness.capabilities import (
    FALLBACK,
    NATIVE,
    mode_override,
    resolve_mode,
    supports_native_tools,
)


def test_unknown_model_defaults_to_fallback():
    assert resolve_mode("mystery-model", {}) == FALLBACK
    assert supports_native_tools("mystery-model", {}) is False


def test_declared_capability_wins():
    assert resolve_mode("m", {"tool_use": True}) == NATIVE
    assert resolve_mode("m", {"tool_use": False}) == FALLBACK


def test_env_overrides(monkeypatch):
    monkeypatch.setattr(settings, "harness_tool_mode", "native")
    assert mode_override("anything") == NATIVE
    monkeypatch.setattr(settings, "harness_tool_mode", "")

    monkeypatch.setattr(settings, "harness_fallback_models", ["a", "b"])
    monkeypatch.setattr(settings, "harness_native_models", ["c"])
    assert mode_override("b") == FALLBACK
    assert mode_override("c") == NATIVE
    assert mode_override("d") is None
    assert resolve_mode("b", {"tool_use": True}) == FALLBACK


def test_env_matches_model_without_provider_prefix(monkeypatch):
    monkeypatch.setattr(settings, "harness_fallback_models", ["qwen2.5:3b"])
    assert mode_override("ollama/qwen2.5:3b") == FALLBACK


def test_invalid_force_mode_ignored(monkeypatch):
    monkeypatch.setattr(settings, "harness_tool_mode", "whatever")
    assert mode_override("m") is None
