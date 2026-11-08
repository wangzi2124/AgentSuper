"""G-08: local OpenAI-compatible discovery marks capabilities unknown."""

from __future__ import annotations

import httpx

from config import settings
from models import local_discovery


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_discover_local(monkeypatch):
    local_discovery.reset_cache()
    monkeypatch.setattr(settings, "local_discovery_enabled", True)
    monkeypatch.setattr(settings, "lmstudio_api_base", "http://lm:1234")
    monkeypatch.setattr(settings, "vllm_api_base", "http://vllm:8000")
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "lm:1234" in url:
            return _Resp({"data": [{"id": "qwen"}]})
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", fake_get)
    models = local_discovery.discover_local(force=True)
    assert models == [
        {
            "id": "lmstudio/qwen",
            "provider": "lmstudio",
            "name": "qwen",
            "capabilities": {},
            "origin": "local",
        }
    ]
    assert any("lm:1234" in url for url in calls)


def test_discovery_disabled(monkeypatch):
    local_discovery.reset_cache()
    monkeypatch.setattr(settings, "local_discovery_enabled", False)
    assert local_discovery.discover_local(force=True) == []
