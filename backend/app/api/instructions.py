"""Read-only project instruction sources API (opencode gap G-01).

The API never returns instruction bodies: clients only see *which* sources were
found, whether they were loaded, and their size. Editing stays on the
filesystem, which keeps the server the single source of truth.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from config import settings
from app.deps import default_session_directory, get_context

router = APIRouter(tags=["Instructions"])


@router.get("/instructions")
async def list_instructions(request: Request, session_id: str = "") -> dict:
    """Metadata for the effective instruction sources of one session."""
    from prompts.instructions import get_instruction_resolver

    ctx = get_context(request)
    resolver = get_instruction_resolver()
    if session_id:
        sess = ctx.sessions.get(session_id)
        directory = getattr(sess, "directory", None) if sess is not None else None
        workspace = directory or default_session_directory(session_id)
        model_id = ctx.session_model(session_id) or ""
    else:
        workspace = str(Path.cwd())
        model_id = ""

    sources = resolver.describe(workspace, model_id)
    loaded = [item for item in sources if item.get("loaded")]
    effective = resolver.resolve(workspace, model_id)
    return {
        "enabled": bool(getattr(settings, "instructions_enabled", True)),
        "workspace": str(workspace),
        "sources": sources,
        "loaded_count": len(loaded),
        "total_chars": sum(len(block.content) for block in effective),
        "total_tokens": sum(int(block.tokens) for block in effective),
    }


__all__ = ["router"]
