from __future__ import annotations

import pytest

from app.local_models import store


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "models_root", lambda: tmp_path)
    monkeypatch.setattr(store, "_jobs", None)
    monkeypatch.setattr(store, "_jobs_path", None)
    yield
    store._jobs = None
    store._jobs_path = None


def test_job_lifecycle_persists():
    job = store.new_job(repo_id="org/m", file="m.gguf", files=["m.gguf"], total=100)
    assert job["state"] == "pending"
    store.update(job["id"], state="downloading", downloaded=50)
    got = store.get(job["id"])
    assert got["downloaded"] == 50
    assert [j["id"] for j in store.list_jobs()] == [job["id"]]
    assert (store.models_root() / "jobs.json").exists()
    assert store.delete(job["id"]) is True
    assert store.get(job["id"]) is None


def test_active_jobs_marked_interrupted_after_restart():
    job = store.new_job(repo_id="org/m", file="m.gguf", files=["m.gguf"], total=100)
    store.update(job["id"], state="downloading")
    store._jobs = None
    store._jobs_path = None
    reloaded = store.get(job["id"])
    assert reloaded["state"] == "error"
    assert "中断" in reloaded["error"]


def test_dir_size_bytes(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"1234")
    (tmp_path / "y.bin").write_bytes(b"12")
    assert store.dir_size_bytes(tmp_path) == 6
