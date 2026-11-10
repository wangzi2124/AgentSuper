from __future__ import annotations

import pytest

from app.local_models import downloader, importer, store
from models import catalog


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "models_root", lambda: tmp_path)
    monkeypatch.setattr(store, "_jobs", None)
    monkeypatch.setattr(store, "_jobs_path", None)
    yield
    store._jobs = None
    store._jobs_path = None


def _done_job(entry_id="ollama_chat/m:latest", owner=None):
    job = store.new_job(repo_id="org/m", file="m.gguf", files=["m.gguf"], total=10)
    local = {"ollama_model": "m", "job_id": owner or job["id"]}
    store.update(
        job["id"],
        state="done",
        entry_id=entry_id,
        entry={"id": entry_id, "local": local},
    )
    return store.get(job["id"])


def test_remove_job_drops_model_entry_and_files(monkeypatch):
    job = _done_job()
    job_dir = store.downloads_dir() / job["id"]
    job_dir.mkdir(parents=True)
    (job_dir / "m.gguf").write_bytes(b"x")
    removed = []
    monkeypatch.setattr(importer, "delete_ollama_model", lambda name: removed.append(("ollama", name)))
    monkeypatch.setattr(catalog, "load_config", lambda: {"extra": [job["entry"]], "hidden": []})
    monkeypatch.setattr(catalog, "remove_custom_model", lambda mid: removed.append(("catalog", mid)))

    assert downloader.remove_job(job["id"], delete_files=True) is True
    assert removed == [("ollama", "m"), ("catalog", job["entry_id"])]
    assert not job_dir.exists()
    assert store.get(job["id"]) is None


def test_remove_job_keeps_model_owned_by_another_job(monkeypatch):
    job = _done_job(owner="lm_other")
    removed = []
    monkeypatch.setattr(importer, "delete_ollama_model", lambda name: removed.append(name))
    monkeypatch.setattr(catalog, "load_config", lambda: {"extra": [], "hidden": []})

    assert downloader.remove_job(job["id"], delete_files=True) is True
    assert removed == []
    assert store.get(job["id"]) is None


def test_remove_job_without_files_keeps_model(monkeypatch):
    job = _done_job()
    job_dir = store.downloads_dir() / job["id"]
    job_dir.mkdir(parents=True)
    (job_dir / "m.gguf").write_bytes(b"x")
    removed = []
    monkeypatch.setattr(importer, "delete_ollama_model", lambda name: removed.append(name))

    assert downloader.remove_job(job["id"], delete_files=False) is True
    assert removed == []
    assert (job_dir / "m.gguf").exists()
    assert store.get(job["id"]) is None
