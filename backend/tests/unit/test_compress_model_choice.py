"""压缩模型选择：compress_model / summarization_model 的取值必须真正生效。"""

from __future__ import annotations

from config import settings
from models.router import get_compress_model


def test_compress_model_value_is_used(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "compress_model", "deepseek/deepseek-v4-pro")
    model = get_compress_model(max_tokens=128)
    assert model.model_override == "deepseek/deepseek-v4-pro"
    assert model._endpoint()["model"] == "deepseek/deepseek-v4-pro"


def test_explicit_model_beats_settings(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "compress_model", "deepseek/deepseek-v4-pro")
    model = get_compress_model(max_tokens=128, model="ollama_chat/qwen2.5:3b")
    assert model.model_override == "ollama_chat/qwen2.5:3b"
    assert model._endpoint()["model"] == "ollama_chat/qwen2.5:3b"


def test_default_is_tier_simple(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "")
    monkeypatch.setattr(settings, "compress_model", None)
    model = get_compress_model(max_tokens=128)
    assert model.model_override is None
    assert model.tier == "simple"


def test_proxy_mode_uses_configured_compress_model(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "compress_model", "proxy-compress-alias")
    model = get_compress_model(max_tokens=128)
    endpoint = model._endpoint()
    assert endpoint["model"] == "proxy-compress-alias"
    assert endpoint["api_base"] == "http://localhost:4000"


def test_proxy_mode_without_compress_model_keeps_tier_alias(monkeypatch):
    monkeypatch.setattr(settings, "litellm_proxy_url", "http://localhost:4000")
    monkeypatch.setattr(settings, "compress_model", None)
    model = get_compress_model(max_tokens=128)
    assert model._endpoint()["model"] == "smart-router"
