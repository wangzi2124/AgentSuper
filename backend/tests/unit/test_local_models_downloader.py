from __future__ import annotations

import contextlib

import pytest

from app.local_models import downloader, importer, store
from models import catalog


class _ImmediateThread:
    def __init__(self, target=None, args=(), name=None, daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


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


def _errored_job(entry_id="local/m"):
    job = store.new_job(repo_id="org/m", file="m.gguf", files=["m.gguf"], total=10)
    store.update(job["id"], state="error", error="Ollama 未启动")
    return store.get(job["id"])


def test_retry_import_reuses_downloaded_files(tmp_path, monkeypatch):
    job = _errored_job()
    job_dir = store.downloads_dir() / job["id"]
    job_dir.mkdir(parents=True)
    (job_dir / "m.gguf").write_bytes(b"x")
    calls: list[dict] = []
    monkeypatch.setattr(downloader, "_semaphore", lambda: contextlib.nullcontext())
    monkeypatch.setattr(
        importer, "import_downloaded", lambda **kw: calls.append(kw) or {"id": "local/m"}
    )
    monkeypatch.setattr(downloader.threading, "Thread", _ImmediateThread)

    assert downloader.retry_import(job["id"]) is True
    assert calls and calls[0]["gguf_paths"][0].name == "m.gguf"
    assert store.get(job["id"])["state"] == "done"


def test_retry_import_marks_error_again_when_still_failing(tmp_path, monkeypatch):
    job = _errored_job()
    job_dir = store.downloads_dir() / job["id"]
    job_dir.mkdir(parents=True)
    (job_dir / "m.gguf").write_bytes(b"x")
    monkeypatch.setattr(downloader, "_semaphore", lambda: contextlib.nullcontext())
    monkeypatch.setattr(downloader.threading, "Thread", _ImmediateThread)

    def boom(**_kw):
        raise importer.ImportError_("Ollama 导入失败：连接被拒绝")

    monkeypatch.setattr(importer, "import_downloaded", boom)

    assert downloader.retry_import(job["id"]) is True
    retried = store.get(job["id"])
    assert retried["state"] == "error"
    assert "连接被拒绝" in retried["error"]


def test_retry_import_rejects_non_error_job(tmp_path):
    job = store.new_job(repo_id="org/m", file="m.gguf", files=["m.gguf"], total=10)
    store.update(job["id"], state="done")
    assert downloader.retry_import(job["id"]) is False


def test_retry_import_rejects_missing_files(tmp_path):
    job = _errored_job()
    assert downloader.retry_import(job["id"]) is False


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
