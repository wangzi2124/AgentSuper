"""Background GGUF download runner (resumable, cancellable, quota-guarded)."""

from __future__ import annotations

import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from app.local_models import importer, sources, store

_CHUNK = 1024 * 1024
_sem: threading.BoundedSemaphore | None = None
_sem_lock = threading.Lock()


class Cancelled(RuntimeError):
    pass


def _semaphore() -> threading.BoundedSemaphore:
    global _sem
    with _sem_lock:
        if _sem is None:
            from config import get_settings

            n = int(getattr(get_settings(), "local_model_download_concurrency", 2) or 2)
            _sem = threading.BoundedSemaphore(max(1, n))
        return _sem


def _check_quota(needed: int) -> None:
    from config import get_settings

    quota_gb = float(getattr(get_settings(), "local_model_disk_quota_gb", 0) or 0)
    if quota_gb <= 0:
        return
    used = store.dir_size_bytes(store.downloads_dir())
    if used + max(0, needed) > quota_gb * 1024 ** 3:
        raise RuntimeError(
            f"磁盘配额不足：已用 {used / 1024 ** 3:.2f} GB，"
            f"本次需要 {needed / 1024 ** 3:.2f} GB，上限 {quota_gb:g} GB"
        )


def start(job: dict[str, Any]) -> None:
    thread = threading.Thread(
        target=_run,
        args=(job["id"],),
        name=f"local-model-{job['id']}",
        daemon=True,
    )
    thread.start()


def _run(job_id: str) -> None:
    with _semaphore():
        try:
            _run_inner(job_id)
        except Cancelled:
            store.update(job_id, state="cancelled", speed=0.0)
        except Exception as exc:  # noqa: BLE001
            store.update(job_id, state="error", speed=0.0, error=str(exc)[:800])


def _is_cancelled(job_id: str) -> bool:
    job = store.get(job_id)
    return job is None or job.get("state") == "cancelled"


def _run_inner(job_id: str) -> None:
    job = store.get(job_id)
    if job is None:
        return
    files = [str(f) for f in (job.get("files") or []) if str(f).strip()]
    if not files:
        raise RuntimeError("下载任务缺少文件列表")
    total = int(job.get("total") or 0)
    _check_quota(total)

    job_dir = store.downloads_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    store.update(job_id, state="downloading", error="", downloaded=0, speed=0.0)

    downloaded = 0
    window = {"t": time.time(), "bytes": 0}
    paths: list[Path] = []
    for name in files:
        dest = job_dir / Path(name).name
        url = sources.download_url(str(job.get("source") or ""), str(job.get("repo_id") or ""), name)
        downloaded = _download_file(job_id, url, dest, downloaded, total, window)
        paths.append(dest)

    if _is_cancelled(job_id):
        raise Cancelled()
    store.update(job_id, state="importing", downloaded=total or downloaded, speed=0.0)
    entry = importer.import_downloaded(
        job_id=job_id,
        repo_id=str(job.get("repo_id") or ""),
        file=files[0],
        source=str(job.get("source") or ""),
        gguf_paths=paths,
        display_name=str(job.get("name") or ""),
        context_length=int(job.get("context_length") or 0),
    )
    store.update(job_id, state="done", entry_id=entry["id"], entry=entry, error="", speed=0.0)


def _download_file(
    job_id: str,
    url: str,
    dest: Path,
    downloaded: int,
    total: int,
    window: dict[str, Any],
) -> int:
    from net.egress import urllib_opener

    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "agentsuper/0.1"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib_opener().open(req, timeout=120) as resp:
        status = getattr(resp, "status", 200)
        if existing and status != 206:
            existing = 0
        mode = "ab" if existing else "wb"
        with open(part, mode) as fh:
            while True:
                if _is_cancelled(job_id):
                    raise Cancelled()
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                _progress(job_id, downloaded, total, window)
    if part.exists():
        os.replace(part, dest)
    return downloaded


def _progress(job_id: str, downloaded: int, total: int, window: dict[str, Any]) -> None:
    now = time.time()
    if now - window["t"] < 0.5:
        return
    dt = now - window["t"]
    speed = (downloaded - window["bytes"]) / dt if dt > 0 else 0.0
    window["t"], window["bytes"] = now, downloaded
    store.update(job_id, downloaded=downloaded, total=total, speed=round(speed, 1))


def cancel(job_id: str) -> bool:
    job = store.get(job_id)
    if job is None:
        return False
    if job.get("state") in ("done", "error", "cancelled"):
        return False
    store.update(job_id, state="cancelled", speed=0.0)
    return True


def remove_job(job_id: str, *, delete_files: bool = True) -> bool:
    job = store.get(job_id)
    if job is None:
        return False
    cancel(job_id)
    if delete_files:
        import shutil

        _remove_imported_model(job)
        target = store.downloads_dir() / job_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    return store.delete(job_id)


def _remove_imported_model(job: dict[str, Any]) -> None:
    """Drop the imported Ollama model + catalog entry that belong to this job."""
    entry_id = str(job.get("entry_id") or "")
    if not entry_id:
        return
    job_entry = job.get("entry") or {}
    job_local = job_entry.get("local") or {}
    if job_local.get("job_id") and job_local["job_id"] != job["id"]:
        return
    from models import catalog

    cfg = catalog.load_config()
    persisted = next((e for e in (cfg.get("extra") or []) if e.get("id") == entry_id), None)
    persisted_local = (persisted or {}).get("local") or {}
    owner = str(persisted_local.get("job_id") or "")
    if owner and owner != job["id"]:
        return
    name = str(persisted_local.get("ollama_model") or job_local.get("ollama_model") or "")
    if not name and persisted is None and not job_entry:
        return
    if name:
        try:
            importer.delete_ollama_model(name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"移除 Ollama 模型失败：{exc}") from exc
    catalog.remove_custom_model(entry_id)
