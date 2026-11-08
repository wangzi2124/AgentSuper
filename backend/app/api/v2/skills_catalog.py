"""Skill catalog install API (opencode gap G-17)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.deps import get_context
from app.skills.catalog import install_catalog

router = APIRouter(prefix="/v2", tags=["Skills"])


@router.post("/skills/catalog")
async def install_skill_catalog(request: Request) -> dict:
    ctx = get_context(request)
    payload = await request.json()
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    loader = getattr(ctx, "skill_loader", None)
    if loader is None:
        raise HTTPException(status_code=503, detail="skill loader unavailable")
    names = payload.get("names") if isinstance(payload.get("names"), list) else None
    result = install_catalog(url, loader.skills_dir, names=names)
    loader.load_all()
    return result


__all__ = ["router"]
