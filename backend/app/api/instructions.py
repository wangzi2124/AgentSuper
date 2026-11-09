"""Project instruction sources API (opencode gap G-01).

Read-only diagnostics plus project-level management: the UI can add or remove
instruction files/globs/URLs for one session workspace. Entry bodies are still
never returned; only metadata about discovered sources is exposed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.deps import get_context, is_auto_workspace
from config import get_settings
from prompts.instructions import (
    add_workspace_instruction,
    detect_project_root,
    get_instruction_resolver,
    read_workspace_auto_discover,
    read_workspace_instructions,
    remove_workspace_instruction,
    write_workspace_auto_discover,
)

router = APIRouter(tags=["Instructions"])


def _workspace(ctx, session_id: str, workspace: str = "") -> tuple[Path, bool]:
    """Resolve the instruction workspace like opencode: project root, not temp."""
    if workspace.strip():
        path = Path(workspace.strip().strip('"').strip("'")).expanduser()
        if not path.is_dir():
            raise HTTPException(status_code=400, detail=f"workspace not found: {workspace}")
        return path, True
    if session_id:
        sess = ctx.sessions.get(session_id)
        directory = getattr(sess, "directory", None) if sess is not None else None
        if directory and not is_auto_workspace(directory):
            return Path(directory).expanduser(), True
        return detect_project_root(Path.cwd()), True
    return detect_project_root(Path.cwd()), True


@router.get("/instructions")
async def list_instructions(
    request: Request, session_id: str = "", workspace: str = ""
) -> dict:
    """Metadata for the effective instruction sources of one session."""
    ctx = get_context(request)
    workspace_path, project = _workspace(ctx, session_id, workspace)
    model_id = ctx.session_model(session_id) if session_id else ""
    resolver = get_instruction_resolver()
    sources = resolver.describe(str(workspace_path), model_id)
    loaded = [item for item in sources if item.get("loaded")]
    effective = resolver.resolve(str(workspace_path), model_id)
    return {
        "enabled": bool(getattr(get_settings(), "instructions_enabled", True)),
        "workspace": str(workspace_path),
        "scope": "project" if project else "global",
        "entries": read_workspace_instructions(workspace_path) if project else [],
        "auto_discover": read_workspace_auto_discover(workspace_path) if project else True,
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


@router.post("/instructions/entries")
async def add_instruction_entry(
    request: Request, session_id: str = "", workspace: str = ""
) -> dict:
    ctx = get_context(request)
    workspace_path, _project = _workspace(ctx, session_id, workspace)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    entry = _normalize_entry(payload.get("entry"), workspace_path)
    entries = add_workspace_instruction(workspace_path, entry)
    return {"entries": entries, "workspace": str(workspace_path), "entry": entry}


@router.delete("/instructions/entries")
async def remove_instruction_entry(
    request: Request, session_id: str = "", workspace: str = ""
) -> dict:
    ctx = get_context(request)
    workspace_path, _project = _workspace(ctx, session_id, workspace)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty body is a validation error below
        payload = {}
    entry = str((payload or {}).get("entry") or "").strip()
    if not entry:
        raise HTTPException(status_code=400, detail="entry is required")
    entries = remove_workspace_instruction(workspace_path, entry)
    return {"entries": entries, "workspace": str(workspace_path), "entry": entry}


@router.patch("/instructions/settings")
async def update_instruction_settings(
    request: Request, session_id: str = "", workspace: str = ""
) -> dict:
    ctx = get_context(request)
    workspace_path, _project = _workspace(ctx, session_id, workspace)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    value = payload.get("auto_discover")
    if not isinstance(value, bool):
        raise HTTPException(status_code=400, detail="auto_discover must be a boolean")
    write_workspace_auto_discover(workspace_path, value)
    return {"auto_discover": value, "workspace": str(workspace_path)}


__all__ = ["router"]
