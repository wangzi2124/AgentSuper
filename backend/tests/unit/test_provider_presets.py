from __future__ import annotations

import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import models as models_api
from app.api.deps import require_admin
from models import catalog_db, presets


def _isolate(monkeypatch, tmp_path):
    db_path = tmp_path / "model_catalog.db"
    legacy_path = tmp_path / "provider_presets.json"
    monkeypatch.setattr(catalog_db, "catalog_db_path", lambda: db_path)
    monkeypatch.setattr(presets, "presets_path", lambda: legacy_path)
    return db_path, legacy_path


def test_builtins_seeded_into_db_and_crud_persist(monkeypatch, tmp_path):
    db_path, legacy_path = _isolate(monkeypatch, tmp_path)

    items = presets.list_presets()
    assert any(p["id"] == "google" and p["type"] == "openai" for p in items)
    assert any(p["id"] == "ollama-cloud" and p["type"] == "ollama" for p in items)
    assert any(p["id"] == "zhipu" and p["litellm_prefix"] == "openai" for p in items)
    assert not legacy_path.exists()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM provider_presets").fetchall()
        seeded = conn.execute(
            "SELECT value FROM provider_presets_meta WHERE key = 'provider_presets_seeded'"
        ).fetchone()
    assert {r[0] for r in rows} == {p["id"] for p in items}
    assert seeded == ("1",)

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

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM provider_presets WHERE id = 'my_provider'"
        ).fetchone()[0] == 0


def test_legacy_json_migrated_once_and_validation(monkeypatch, tmp_path):
    db_path, legacy_path = _isolate(monkeypatch, tmp_path)
    legacy_path.write_text(json.dumps({"presets": [
        {"id": "custom", "label": "Changed", "type": "openai"},
    ]}), encoding="utf-8")

    items = presets.list_presets()
    assert next(p for p in items if p["id"] == "custom")["label"] == "Changed"
    assert any(p["id"] == "openai" for p in items)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT label FROM provider_presets WHERE id = 'custom'"
        ).fetchone()[0] == "Changed"

    try:
        presets.upsert_preset({"id": "Bad ID", "label": "Bad"})
    except ValueError as exc:
        assert "字典 ID" in str(exc)
    else:
        raise AssertionError("invalid preset id should be rejected")

    assert not hasattr(presets, "reset_presets")


def test_presets_api_crud_and_no_reset(monkeypatch, tmp_path):
    db_path, _ = _isolate(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(models_api.router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: None

    with TestClient(app) as client:
        listed = client.get("/api/models/presets")
        assert listed.status_code == 200
        assert listed.json()["code"] == 0
        assert listed.json()["data"]["source_path"] == str(db_path)
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
        assert reset.status_code in (404, 405)
