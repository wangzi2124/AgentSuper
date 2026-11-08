"""Network egress diagnostics (opencode gap G-12)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from net.egress import diagnose, egress_config

router = APIRouter(prefix="/v2", tags=["Network"])


@router.get("/network")
async def network_config() -> dict:
    config = egress_config()
    return {
        "proxy": bool(config.proxy),
        "no_proxy": bool(config.no_proxy),
        "ca_bundle": bool(config.ca_bundle),
        "verify": config.verify,
        "timeout": config.timeout,
    }


@router.get("/network/diagnose")
async def network_diagnose(url: str) -> dict:
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    return diagnose(url)


__all__ = ["router"]
