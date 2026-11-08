"""G-18: generic settings schema, validation and rollback."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2 import config as config_api
from config import settings


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(config_api, "require_admin", lambda request: None)
    monkeypatch.setattr(config_api, "persist_settings", lambda values: None)
    config_api._snapshots.clear()
    app = FastAPI()
    app.include_router(config_api.router, prefix="/api")
    return TestClient(app)


def test_schema_and_values(monkeypatch):
    client = _client(monkeypatch)
    body = client.get("/api/v2/config").json()
    assert "model" in body["schema"]
    assert body["values"]["llm_model"] == settings.llm_model
    assert "tavily_api_key" not in body["values"]


def test_patch_validates_and_applies(monkeypatch):
    client = _client(monkeypatch)
    old = settings.websearch_provider
    try:
        bad = client.patch("/api/v2/config", json={"websearch_provider": 123})
        assert bad.status_code == 400
        unknown = client.patch("/api/v2/config", json={"nope": 1})
        assert unknown.status_code == 400
        ok = client.patch("/api/v2/config", json={"values": {"websearch_provider": "bing"}})
        assert ok.status_code == 200
        assert settings.websearch_provider == "bing"
        rolled = client.post("/api/v2/config/rollback")
        assert rolled.status_code == 200
        assert settings.websearch_provider == old
    finally:
        settings.websearch_provider = old


def test_sensitive_redaction():
    assert config_api._dump("tavily_api_key", "secret") == "<redacted>"
    assert config_api._dump("tavily_api_key", "") == ""
    assert config_api._dump("llm_model", "x") == "x"


def test_persist_settings_roundtrip(tmp_path, monkeypatch):
    import config as config_mod

    path = tmp_path / "config.json"
    path.write_text('{"llm_model": "old"}', encoding="utf-8")
    monkeypatch.setattr(config_mod, "config_file_path", lambda: path)
    config_mod.persist_settings({"websearch_provider": "bing"})
    loaded = config_mod.parse_jsonc(path.read_text(encoding="utf-8"))
    assert loaded["llm_model"] == "old"
    assert loaded["websearch_provider"] == "bing"
