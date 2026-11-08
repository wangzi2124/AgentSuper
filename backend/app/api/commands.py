"""Slash command discovery API (opencode gap G-02)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.commands import load_commands
from app.deps import default_session_directory, get_context

router = APIRouter(prefix="/v2", tags=["Commands"])


def _workspace(ctx, session_id: str) -> str | None:
    if not session_id:
        return None
    sess = ctx.sessions.get(session_id)
    directory = getattr(sess, "directory", None) if sess is not None else None
    return directory or default_session_directory(session_id)


@router.get("/commands")
async def list_commands(request: Request, session_id: str = "") -> dict:
    ctx = get_context(request)
    registry = load_commands(_workspace(ctx, session_id))
    return {
        "commands": [
            {
                "name": command.name,
                "description": command.description,
                "agent": command.agent,
                "model": command.model,
                "subtask": command.subtask,
                "files": list(command.files),
                "source": command.source,
                "path": command.path,
            }
            for command in registry.list()
        ]
    }


__all__ = ["router"]
