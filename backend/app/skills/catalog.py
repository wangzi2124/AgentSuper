"""HTTP skill catalogs (opencode gap G-17)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def fetch_catalog(url: str, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    import httpx

    from net.egress import httpx_client_kwargs

    kwargs = httpx_client_kwargs()
    kwargs.pop("timeout", None)
    response = httpx.get(url, timeout=timeout, follow_redirects=True, **kwargs)
    response.raise_for_status()
    payload = response.json()
    skills = payload.get("skills") if isinstance(payload, dict) else payload
    if not isinstance(skills, list):
        raise ValueError("catalog must be a list or {skills: [...]}")
    out: list[dict[str, Any]] = []
    for item in skills:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("url") or item.get("raw") or "").strip()
        if not name or not source:
            continue
        out.append(
            {
                "name": name,
                "description": str(item.get("description") or ""),
                "url": source,
            }
        )
    return out


def install_skill(url: str, name: str, target_root: str | Path, *, timeout: float = 15.0) -> Path:
    import httpx

    from net.egress import httpx_client_kwargs

    if not _SAFE.match(name):
        raise ValueError(f"unsafe skill name: {name}")
    kwargs = httpx_client_kwargs()
    kwargs.pop("timeout", None)
    response = httpx.get(url, timeout=timeout, follow_redirects=True, **kwargs)
    response.raise_for_status()
    body = response.text
    target = Path(target_root) / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def install_catalog(
    url: str, target_root: str | Path, *, names: list[str] | None = None
) -> dict[str, Any]:
    wanted = set(names or [])
    installed: list[str] = []
    errors: list[str] = []
    for item in fetch_catalog(url):
        if wanted and item["name"] not in wanted:
            continue
        try:
            install_skill(item["url"], item["name"], target_root)
            installed.append(item["name"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{item['name']}: {exc}")
    return {"installed": installed, "errors": errors}


__all__ = ["fetch_catalog", "install_catalog", "install_skill"]
