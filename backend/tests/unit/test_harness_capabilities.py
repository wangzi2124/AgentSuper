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
    monkeypatch.setenv("HARNESS_TOOL_MODE", "native")
    assert mode_override("anything") == NATIVE
    monkeypatch.delenv("HARNESS_TOOL_MODE")

    monkeypatch.setenv("HARNESS_FALLBACK_MODELS", "a, b")
    monkeypatch.setenv("HARNESS_NATIVE_MODELS", "c")
    assert mode_override("b") == FALLBACK
    assert mode_override("c") == NATIVE
    assert mode_override("d") is None
    assert resolve_mode("b", {"tool_use": True}) == FALLBACK


def test_env_matches_model_without_provider_prefix(monkeypatch):
    monkeypatch.setenv("HARNESS_FALLBACK_MODELS", "qwen2.5:3b")
    assert mode_override("ollama/qwen2.5:3b") == FALLBACK


def test_invalid_force_mode_ignored(monkeypatch):
    monkeypatch.setenv("HARNESS_TOOL_MODE", "whatever")
    assert mode_override("m") is None
