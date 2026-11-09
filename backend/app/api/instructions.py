"""Project instruction sources API (opencode gap G-01).

Read-only diagnostics plus project-level management: the UI can add or remove
instruction files/globs/URLs for one session workspace. Entry bodies are still
never returned; only metadata about discovered sources is exposed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.deps import default_session_directory, get_context
from config import get_settings
from prompts.instructions import (
    add_workspace_instruction,
    get_instruction_resolver,
    read_workspace_instructions,
    remove_workspace_instruction,
)

router = APIRouter(tags=["Instructions"])


def _workspace(ctx, session_id: str) -> tuple[Path, bool]:
    if session_id:
        sess = ctx.sessions.get(session_id)
        directory = getattr(sess, "directory", None) if sess is not None else None
        workspace = directory or default_session_directory(session_id)
        return Path(workspace).expanduser(), True
    return Path.cwd(), False


@router.get("/instructions")
async def list_instructions(request: Request, session_id: str = "") -> dict:
    """Metadata for the effective instruction sources of one session."""
    ctx = get_context(request)
    workspace, project = _workspace(ctx, session_id)
    model_id = ctx.session_model(session_id) if session_id else ""
    resolver = get_instruction_resolver()
    sources = resolver.describe(str(workspace), model_id)
    loaded = [item for item in sources if item.get("loaded")]
    effective = resolver.resolve(str(workspace), model_id)
    return {
        "enabled": bool(getattr(get_settings(), "instructions_enabled", True)),
        "workspace": str(workspace),
        "scope": "project" if project else "global",
        "entries": read_workspace_instructions(workspace) if project else [],
        "sources": sources,
        "loaded_count": len(loaded),
        "total_chars": sum(len(block.content) for block in effective),
        "total_tokens": sum(int(block.tokens) for block in effective),
    }


def _has_glob(value: str) -> bool:
    return any(ch in value for ch in "*?[")


def _normalize_entry(entry: object, workspace: Path) -> str:
    text = str(entry or "").strip().strip('"').strip("'")
    if not text:
        raise HTTPException(status_code=400, detail="entry is required")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="entry is too long")
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return text
    if "://" in text:
        raise HTTPException(status_code=400, detail="only http(s) URLs are supported")

    expanded = Path(text).expanduser()
    if expanded.is_absolute() and _has_glob(text):
        raise HTTPException(status_code=400, detail="glob must be relative to the workspace")

    try:
        ws_resolved = workspace.resolve()
    except OSError:
        ws_resolved = workspace

    if _has_glob(text):
        if ".." in Path(text).parts:
            raise HTTPException(status_code=400, detail="glob must stay inside the workspace")
        try:
            matches = [item for item in workspace.glob(text) if item.is_file()]
        except (ValueError, OSError):
            matches = []
        if not matches:
            raise HTTPException(status_code=400, detail="glob matched no files")
        return Path(text).as_posix()

    target = expanded if expanded.is_absolute() else workspace / expanded
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target
    try:
        relative = resolved.relative_to(ws_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="file must be inside the workspace")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"file not found: {text}")
    return relative.as_posix()


def _require_project(session_id: str, project: bool) -> None:
    if not project:
        raise HTTPException(
            status_code=400,
            detail="session_id is required to manage project instructions",
        )


@router.post("/instructions/entries")
async def add_instruction_entry(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    workspace, project = _workspace(ctx, session_id)
    _require_project(session_id, project)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    entry = _normalize_entry(payload.get("entry"), workspace)
    entries = add_workspace_instruction(workspace, entry)
    return {"entries": entries, "workspace": str(workspace), "entry": entry}


@router.delete("/instructions/entries")
async def remove_instruction_entry(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    workspace, project = _workspace(ctx, session_id)
    _require_project(session_id, project)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty body is a validation error below
        payload = {}
    entry = str((payload or {}).get("entry") or "").strip()
    if not entry:
        raise HTTPException(status_code=400, detail="entry is required")
    entries = remove_workspace_instruction(workspace, entry)
    return {"entries": entries, "workspace": str(workspace), "entry": entry}


__all__ = ["router"]
