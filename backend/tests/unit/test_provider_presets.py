from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import models as models_api
from app.api.deps import require_admin
from models import presets


def _isolate(monkeypatch, tmp_path):
    path = tmp_path / "provider_presets.json"
    monkeypatch.setattr(presets, "presets_path", lambda: path)
    monkeypatch.setattr(presets, "_cache", None)
    monkeypatch.setattr(presets, "_cache_path", "")
    return path


def test_seed_and_crud(monkeypatch, tmp_path):
    path = _isolate(monkeypatch, tmp_path)

    items = presets.list_presets()
    assert any(p["id"] == "google" and p["type"] == "openai" for p in items)
    assert any(p["id"] == "ollama-cloud" and p["type"] == "ollama" for p in items)
    assert any(p["id"] == "zhipu" and p["litellm_prefix"] == "openai" for p in items)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["presets"]

    created = presets.upsert_preset({
        "id": "my_provider",
        "label": "My Provider",
        "name": "mine",
        "type": "openai",
        "api_base": "https://api.example.com/v1",
        "litellm_prefix": "openai",
        "group": "custom",
    })
    assert created["id"] == "my_provider"
    assert any(p["id"] == "my_provider" for p in presets.list_presets())

    updated = presets.upsert_preset({"label": "My Provider v2"}, "my_provider")
    assert updated["label"] == "My Provider v2"
    assert updated["api_base"] == "https://api.example.com/v1"

    assert presets.remove_preset("my_provider") is True
    assert presets.remove_preset("my_provider") is False
    assert not any(p["id"] == "my_provider" for p in presets.list_presets())


def test_validation_and_reset(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    try:
        presets.upsert_preset({"id": "Bad ID", "label": "Bad"})
    except ValueError as exc:
        assert "字典 ID" in str(exc)
    else:
        raise AssertionError("invalid preset id should be rejected")

    presets.upsert_preset({"id": "custom", "label": "Changed", "type": "openai"})
    assert next(p for p in presets.list_presets() if p["id"] == "custom")["label"] == "Changed"

    default_items = presets.reset_presets()
    assert next(p for p in default_items if p["id"] == "custom")["label"] != "Changed"
    assert any(p["id"] == "openai" for p in presets.list_presets())


def test_presets_api_crud(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(models_api.router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: None

    with TestClient(app) as client:
        listed = client.get("/api/models/presets")
        assert listed.status_code == 200
        assert listed.json()["code"] == 0
        assert any(p["id"] == "google" for p in listed.json()["data"]["presets"])

        created = client.post("/api/models/presets", json={
            "id": "api_provider",
            "label": "API Provider",
            "name": "api-provider",
            "group": "custom",
            "type": "openai",
            "api_base": "https://api.example.com/v1",
            "package": "openai",
            "litellm_prefix": "openai",
        })
        assert created.status_code == 200
        assert created.json()["data"]["preset"]["id"] == "api_provider"

        updated = client.put("/api/models/presets/api_provider", json={"label": "Updated"})
        assert updated.json()["data"]["preset"]["label"] == "Updated"
        assert updated.json()["data"]["preset"]["api_base"] == "https://api.example.com/v1"

        removed = client.delete("/api/models/presets/api_provider")
        assert removed.json()["data"]["removed"] is True

        reset = client.post("/api/models/presets/reset")
        assert reset.status_code == 200
        assert any(p["id"] == "google" for p in reset.json()["data"]["presets"])
