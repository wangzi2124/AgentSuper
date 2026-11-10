"""Local model download job store (JSON under the unified data root).

Jobs are process-local background tasks; the JSON file keeps progress/history
across restarts and is intentionally simple (low-frequency writes).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] | None = None
_jobs_path: Path | None = None

_ACTIVE_STATES = {"pending", "running", "downloading", "importing"}


def models_root() -> Path:
    from config import get_settings
    from storage.paths import data_path

    raw = getattr(get_settings(), "local_models_dir", "") or "local_models"
    root = data_path(raw, "local_models")
    root.mkdir(parents=True, exist_ok=True)
    return root


def downloads_dir() -> Path:
    root = models_root() / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def jobs_path() -> Path:
    return models_root() / "jobs.json"


def _load_locked() -> dict[str, dict[str, Any]]:
    global _jobs, _jobs_path
    path = jobs_path()
    if _jobs is not None and _jobs_path == path:
        return _jobs
    jobs: dict[str, dict[str, Any]] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                jobs = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError):
            jobs = {}
    for job in jobs.values():
        if job.get("state") in _ACTIVE_STATES:
            job["state"] = "error"
            job["error"] = "服务重启，任务已中断"
    _jobs, _jobs_path = jobs, path
    return _jobs


def _save_locked() -> None:
    jobs = _jobs or {}
    path = jobs_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def new_job(**fields: Any) -> dict[str, Any]:
    with _lock:
        jobs = _load_locked()
        jid = "lm_" + uuid.uuid4().hex[:12]
        now = int(time.time() * 1000)
        job: dict[str, Any] = {
            "id": jid,
            "created_at": now,
            "updated_at": now,
            "state": "pending",
            "downloaded": 0,
            "total": 0,
            "speed": 0.0,
            "error": "",
            "entry_id": "",
        }
        job.update(fields)
        jobs[jid] = job
        _save_locked()
        return dict(job)


def update(job_id: str, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        jobs = _load_locked()
        job = jobs.get(job_id)
        if job is None:
            return None
        job.update(fields)
        job["updated_at"] = int(time.time() * 1000)
        _save_locked()
        return dict(job)


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _load_locked().get(job_id)
        return dict(job) if job else None


def list_jobs() -> list[dict[str, Any]]:
    with _lock:
        jobs = list(_load_locked().values())
    return sorted(jobs, key=lambda j: int(j.get("created_at") or 0), reverse=True)


def delete(job_id: str) -> bool:
    with _lock:
        jobs = _load_locked()
        if job_id not in jobs:
            return False
        jobs.pop(job_id, None)
        _save_locked()
        return True


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                continue
    return total
