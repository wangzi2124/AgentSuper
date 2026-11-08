"""Local OpenAI-compatible model discovery (opencode gap G-08)."""

from __future__ import annotations

from fastapi import APIRouter

from models.local_discovery import discover_local

router = APIRouter(prefix="/v2", tags=["Models"])


@router.get("/local-models")
async def local_models(refresh: bool = False) -> dict:
    return {"models": discover_local(force=refresh)}


__all__ = ["router"]
