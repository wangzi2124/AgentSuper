"""Web search provider status (opencode gap G-13)."""

from __future__ import annotations

from fastapi import APIRouter

from config import settings
from execution.tools import websearch_policy

router = APIRouter(prefix="/v2", tags=["WebSearch"])


@router.get("/websearch")
async def websearch_status() -> dict:
    available = [item.name for item in websearch_policy.candidates(provider="auto")]
    return {
        "enabled": bool(getattr(settings, "websearch_enabled", True)),
        "provider": str(getattr(settings, "websearch_provider", "auto")),
        "cooldown_seconds": float(getattr(settings, "websearch_cooldown_seconds", 60.0)),
        "available": available,
        "cooldown": websearch_policy.cooldowns(),
    }


__all__ = ["router"]
