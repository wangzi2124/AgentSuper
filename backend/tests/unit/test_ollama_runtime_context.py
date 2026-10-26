"""单元测试：Ollama 运行时窗口解析（/api/ps）与 num_ctx 下发策略。"""

from __future__ import annotations

import pytest

from models import catalog


@pytest.fixture(autouse=True)
def _reset_runtime_cache(monkeypatch):
    catalog._runtime_ctx_last.clear()
    catalog._ps_cache = None
    catalog._ps_cache_at = 0.0
    monkeypatch.setattr(catalog, "local_endpoint_alive", lambda *a, **k: True)
    yield
    catalog._runtime_ctx_last.clear()
    catalog._ps_cache = None
    catalog._ps_cache_at = 0.0


def _patch_ps(monkeypatch, models):
    monkeypatch.setattr(catalog, "_ps_snapshot", lambda force=False: dict(models))


def test_no_explicit_override_means_no_num_ctx(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    creds = catalog.provider_api("ollama_chat/deepseek-r1:7b")
    opts = creds["options"]
    assert "num_ctx" not in opts


def test_explicit_override_sends_num_ctx(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "load_config",
        lambda: {"overrides": {"ollama_chat/deepseek-r1:7b": {"context_length": 16384}}},
    )
    creds = catalog.provider_api("ollama_chat/deepseek-r1:7b")
    assert creds["options"]["num_ctx"] == 16384


def test_effective_window_prefers_explicit_override(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "load_config",
        lambda: {"overrides": {"ollama_chat/x": {"context_length": 4096}}},
    )
    _patch_ps(monkeypatch, {"x": 16384})
    assert catalog.effective_context_length("ollama_chat/x", fallback=8192) == 4096


def test_effective_window_uses_runtime_ps(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    _patch_ps(monkeypatch, {"deepseek-r1:7b": 16384})
    assert catalog.effective_context_length("ollama_chat/deepseek-r1:7b", fallback=8192) == 16384


def test_effective_window_falls_back_when_unloaded(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    _patch_ps(monkeypatch, {})
    assert catalog.effective_context_length("ollama_chat/deepseek-r1:7b", fallback=8192) == 8192


def test_last_known_survives_unload(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    _patch_ps(monkeypatch, {"deepseek-r1:7b": 16384})
    assert catalog.probe_runtime_context_length("ollama_chat/deepseek-r1:7b") == 16384
    # 模型被卸载：/api/ps 不再有该条目，但 last-known 继续生效
    _patch_ps(monkeypatch, {})
    assert (
        catalog.probe_runtime_context_length("ollama_chat/deepseek-r1:7b", force=True) == 16384
    )


def test_ps_probe_throttled(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        return FakeResp(b'{"models":[{"name":"x","context_length":16384}]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert catalog.probe_runtime_context_length("ollama_chat/x") == 16384
    assert catalog.probe_runtime_context_length("ollama_chat/x") == 16384
    assert calls["n"] == 1


def test_non_ollama_uses_declared_window(monkeypatch):
    monkeypatch.setattr(
        catalog, "read_limits", lambda mid: {"context_length": 160000, "max_output_tokens": 0}
    )
    assert catalog.effective_context_length("deepseek/deepseek-v4-flash") == 160000


def test_runtime_context_lengths_batch_uses_snapshot(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    monkeypatch.setattr(
        catalog,
        "read_limits",
        lambda mid: {"context_length": 160000, "max_output_tokens": 0},
    )
    _patch_ps(monkeypatch, {"deepseek-r1:7b": 16384})
    got = catalog.runtime_context_lengths(
        ["ollama_chat/deepseek-r1:7b", "ollama_chat/unloaded", "deepseek/deepseek-v4-flash"]
    )
    assert got["ollama_chat/deepseek-r1:7b"] == 16384
    assert got["ollama_chat/unloaded"] == 4096  # settings.ollama_num_ctx 回落
    assert got["deepseek/deepseek-v4-flash"] == 160000


def test_runtime_context_lengths_explicit_override(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "load_config",
        lambda: {"overrides": {"ollama_chat/x": {"context_length": 4096}}},
    )
    _patch_ps(monkeypatch, {"x": 16384})
    got = catalog.runtime_context_lengths(["ollama_chat/x"])
    assert got["ollama_chat/x"] == 4096


def test_annotate_runtime_context_keeps_declared_value(monkeypatch):
    monkeypatch.setattr(catalog, "load_config", lambda: {"overrides": {}})
    _patch_ps(monkeypatch, {"deepseek-r1:7b": 16384})
    entries = [{"id": "ollama_chat/deepseek-r1:7b", "context_length": 131072}]
    out = catalog.annotate_runtime_context(entries)
    assert out[0]["context_length"] == 131072
    assert out[0]["runtime_context_length"] == 16384
