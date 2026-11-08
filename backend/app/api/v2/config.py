"""Generic settings schema, validation, redaction and rollback (G-18)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import TypeAdapter

from app.api.deps import require_admin
from config import Settings, persist_settings, settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v2", tags=["Config"])

SENSITIVE_MARKERS = ("api_key", "token", "secret", "password", "credential")

GROUPS: dict[str, tuple[str, ...]] = {
    "model": (
        "llm_model",
        "llm_max_tokens",
        "tool_concurrency",
        "session_budget",
    ),
    "compaction": (
        "compaction_auto",
        "compaction_keep_tokens",
        "compaction_buffer",
        "compaction_tail_turns",
        "compaction_min_gain_tokens",
        "compress_model",
        "compress_model_enabled",
        "compress_max_tokens",
    ),
    "instructions": (
        "instructions_enabled",
        "instructions",
        "instructions_max_chars",
        "instructions_max_tokens",
    ),
    "websearch": (
        "websearch_enabled",
        "websearch_provider",
        "websearch_cooldown_seconds",
    ),
    "network": (
        "egress_proxy",
        "egress_no_proxy",
        "egress_ca_bundle",
        "egress_verify",
        "egress_timeout_seconds",
    ),
    "formatter": ("formatter",),
    "policies": ("policies",),
    "warming": ("warming",),
}

EDITABLE = {name for fields in GROUPS.values() for name in fields}

_snapshots: list[dict[str, Any]] = []


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _dump(name: str, value: Any) -> Any:
    if _is_sensitive(name):
        return "<redacted>" if value else ""
    return value


def _schema() -> dict[str, Any]:
    fields = Settings.model_fields
    out: dict[str, Any] = {}
    for group, names in GROUPS.items():
        entries = []
        for name in names:
            field = fields.get(name)
            if field is None:
                continue
            entries.append(
                {
                    "name": name,
                    "type": str(field.annotation),
                    "default": _dump(name, field.default),
                    "sensitive": _is_sensitive(name),
                }
            )
        out[group] = entries
    return out


@router.get("/config")
async def get_config() -> dict:
    return {
        "schema": _schema(),
        "values": {
            name: _dump(name, getattr(settings, name, None)) for name in sorted(EDITABLE)
        },
        "rollback_available": bool(_snapshots),
        "rollback_depth": len(_snapshots),
    }


@router.patch("/config")
async def patch_config(request: Request) -> dict:
    require_admin(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
    unknown = [name for name in values if name not in EDITABLE]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown fields: {', '.join(sorted(unknown))}")

    validated: dict[str, Any] = {}
    for name, value in values.items():
        annotation = Settings.model_fields[name].annotation
        try:
            validated[name] = TypeAdapter(annotation).validate_python(value)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid {name}: {exc}") from exc

    _snapshots.append({name: getattr(settings, name) for name in validated})
    for name, value in validated.items():
        setattr(settings, name, value)
    try:
        persist_settings(validated)
    except OSError as exc:
        logger.warning("cannot persist settings: %s", exc)
    logger.info("settings updated: %s", ", ".join(sorted(validated)))
    return {
        "updated": sorted(validated),
        "values": {name: _dump(name, getattr(settings, name)) for name in sorted(validated)},
    }


@router.post("/config/rollback")
async def rollback_config(request: Request) -> dict:
    require_admin(request)
    if not _snapshots:
        raise HTTPException(status_code=409, detail="no changes to roll back")
    restored = _snapshots.pop()
    for name, value in restored.items():
        setattr(settings, name, value)
    try:
        persist_settings(restored)
    except OSError as exc:
        logger.warning("cannot persist rollback: %s", exc)
    return {"restored": sorted(restored), "remaining": len(_snapshots)}


__all__ = ["router"]
