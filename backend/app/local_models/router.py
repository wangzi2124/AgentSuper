"""Local model hub API: search, file listing, download jobs, Ollama import."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_admin
from app.api.responses import fail, ok
from app.local_models import downloader, sources, store

router = APIRouter(prefix="/local-models", tags=["Local Models"], dependencies=[Depends(require_admin)])


def _public(job: dict[str, Any]) -> dict[str, Any]:
    total = int(job.get("total") or 0)
    downloaded = int(job.get("downloaded") or 0)
    out = dict(job)
    out["percent"] = round(downloaded * 100.0 / total, 1) if total > 0 else 0.0
    return out


@router.get("/sources")
async def list_sources() -> dict:
    return ok({
        "endpoints": sources.endpoints(),
        "priority": sources.source_priority(),
    })


@router.get("/search")
async def search(q: str = "", source: str = "auto", limit: int = 20) -> dict:
    try:
        models = await run_in_threadpool(sources.search_models, q, source, max(1, min(limit, 50)))
    except sources.SourceError as exc:
        return fail(message=str(exc))
    return ok({"models": models})


@router.get("/files")
async def files(repo_id: str, source: str = "auto") -> dict:
    try:
        entries = await run_in_threadpool(sources.list_gguf_files, repo_id, source)
    except sources.SourceError as exc:
        return fail(message=str(exc))
    return ok({"repo_id": repo_id, "files": entries})


@router.post("/downloads")
async def create_download(req: Request) -> dict:
    body = await req.json()
    repo_id = str(body.get("repo_id") or "").strip()
    file = str(body.get("file") or "").strip()
    if not repo_id or not file:
        return fail(message="repo_id 与 file 必填")
    source = str(body.get("source") or "auto")
    files = [str(x) for x in (body.get("files") or []) if str(x).strip()]
    size = int(body.get("size") or 0)
    if not files:
        try:
            entries = await run_in_threadpool(sources.list_gguf_files, repo_id, source)
        except sources.SourceError as exc:
            return fail(message=str(exc))
        match = next((e for e in entries if e["file"] == file), None)
        if match is None:
            return fail(message=f"未找到 GGUF 文件：{file}")
        files = list(match["files"])
        size = int(match.get("size") or 0)
        source = str(match.get("source") or source)
    job = store.new_job(
        repo_id=repo_id,
        source=source,
        file=file,
        files=files,
        total=size,
        quantization=str(body.get("quantization") or ""),
        name=str(body.get("name") or ""),
        context_length=int(body.get("context_length") or 0),
    )
    downloader.start(job)
    return ok({"job": _public(job)})


@router.get("/downloads")
async def list_downloads() -> dict:
    return ok({"jobs": [_public(j) for j in store.list_jobs()]})


@router.get("/downloads/{job_id}")
async def get_download(job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        return fail(message="任务不存在")
    return ok({"job": _public(job)})


@router.post("/downloads/{job_id}/cancel")
async def cancel_download(job_id: str) -> dict:
    if not downloader.cancel(job_id):
        return fail(message="任务不存在或已结束")
    job = store.get(job_id)
    return ok({"job": _public(job) if job else None})


@router.delete("/downloads/{job_id}")
async def delete_download(job_id: str, delete_files: bool = True) -> dict:
    job = store.get(job_id)
    if job is None:
        return fail(message="任务不存在")
    try:
        removed = await run_in_threadpool(downloader.remove_job, job_id, delete_files=delete_files)
    except Exception as exc:  # noqa: BLE001
        return fail(message=str(exc))
    return ok({"removed": removed, "entry_id": job.get("entry_id") or ""})
