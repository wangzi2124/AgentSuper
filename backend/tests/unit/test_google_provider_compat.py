"""Google Gemini Provider 兼容修复：探测路径与 `google/` 前缀运行时别名。"""

from __future__ import annotations

import json
import urllib.request

from models import catalog

_GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _fake_urlopen(captured):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "gemini-2.5-flash", "owned_by": "google"}]}).encode("utf-8")

    def _urlopen(req, timeout=0):
        captured["url"] = req.full_url
        return _Resp()

    return _urlopen


def _setup_probe(monkeypatch, captured):
    monkeypatch.setattr(catalog, "local_endpoint_alive", lambda _base: True)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(captured))
    monkeypatch.setattr(catalog, "_probe_cache", {})
    monkeypatch.setattr(catalog, "_provider_seen", {})


def test_probe_google_openai_compatible_models_path(monkeypatch):
    captured: dict[str, str] = {}
    _setup_probe(monkeypatch, captured)

    extras = catalog.probe_provider_models("google", _GOOGLE_BASE, "key", set(), force=True)

    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/models"
    assert [e["id"] for e in extras] == ["google/gemini-2.5-flash"]
    assert catalog._provider_seen["google"] == {"google/gemini-2.5-flash"}


def test_probe_regular_openai_base_still_uses_v1_models(monkeypatch):
    captured: dict[str, str] = {}
    _setup_probe(monkeypatch, captured)

    catalog.probe_provider_models("vllm", "http://127.0.0.1:8001/v1", "", set(), force=True)

    assert captured["url"] == "http://127.0.0.1:8001/v1/models"


def test_google_prefix_routes_to_openai_adapter_for_compatible_base(monkeypatch):
    monkeypatch.setattr(catalog, "read_providers", lambda: {
        "google": {"type": "openai", "api_base": _GOOGLE_BASE, "api_key": "k"},
    })
    monkeypatch.setattr(catalog, "lookup", lambda mid: {"id": mid, "provider": "google"})

    creds = catalog.provider_api("google/gemini-2.5-flash")

    assert creds["litellm_model"] == "openai/gemini-2.5-flash"
    assert creds["api_base"] == _GOOGLE_BASE
    assert creds["api_key"] == "k"
    assert creds["is_ollama"] is False


def test_forced_litellm_prefix_routes_custom_namespace(monkeypatch):
    base = "https://open.bigmodel.cn/api/paas/v4"
    monkeypatch.setattr(catalog, "read_providers", lambda: {
        "zhipu": {"type": "openai", "api_base": base, "api_key": "k", "litellm_prefix": "openai"},
    })
    monkeypatch.setattr(catalog, "lookup", lambda mid: {"id": mid, "provider": "zhipu"})

    creds = catalog.provider_api("zhipu/glm-4.6")

    assert creds["litellm_model"] == "openai/glm-4.6"
    assert creds["api_base"] == base
    assert creds["api_key"] == "k"


def test_google_prefix_routes_to_native_gemini_adapter(monkeypatch):
    base = "https://generativelanguage.googleapis.com/v1beta/"
    monkeypatch.setattr(catalog, "read_providers", lambda: {
        "google": {"type": "openai", "api_base": base, "api_key": "k"},
    })
    monkeypatch.setattr(catalog, "lookup", lambda mid: {"id": mid, "provider": "google"})

    creds = catalog.provider_api("google/gemini-2.5-flash")

    assert creds["litellm_model"] == "gemini/gemini-2.5-flash"
    assert creds["api_base"] == base
