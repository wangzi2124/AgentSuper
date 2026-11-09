"""Local OpenAI-compatible endpoint discovery (G-08: LM Studio / vLLM)."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINTS = {
    "lmstudio": "http://127.0.0.1:1234",
    "vllm": "http://127.0.0.1:8000",
}
_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_TTL = 60.0


def _endpoints() -> dict[str, str]:
    from config import get_settings

    lmstudio = str(getattr(get_settings(), "lmstudio_api_base", "") or "").strip()
    vllm = str(getattr(get_settings(), "vllm_api_base", "") or "").strip()
    return {
        "lmstudio": lmstudio or DEFAULT_ENDPOINTS["lmstudio"],
        "vllm": vllm or DEFAULT_ENDPOINTS["vllm"],
    }


def probe_endpoint(provider: str, base: str, *, timeout: float = 2.0) -> list[dict[str, Any]]:
    import httpx

    from net.egress import httpx_client_kwargs

    kwargs = httpx_client_kwargs()
    kwargs.pop("timeout", None)
    url = f"{base.rstrip('/')}/v1/models"
    try:
        response = httpx.get(url, timeout=timeout, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - discovery is best effort
        logger.debug("local discovery %s failed: %s", provider, exc)
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    entries: list[dict[str, Any]] = []
    for item in data or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        entries.append(
            {
                "id": f"{provider}/{item['id']}",
                "provider": provider,
                "name": str(item.get("id")),
                "capabilities": {},
                "origin": "local",
            }
        )
    return entries


def discover_local(*, force: bool = False) -> list[dict[str, Any]]:
    global _CACHE
    from config import get_settings

    if not bool(getattr(get_settings(), "local_discovery_enabled", True)):
        return []
    now = time.monotonic()
    if not force and _CACHE is not None and now - _CACHE[0] < _TTL:
        return list(_CACHE[1])
    entries: list[dict[str, Any]] = []
    for provider, base in _endpoints().items():
        entries.extend(probe_endpoint(provider, base))
    _CACHE = (now, entries)
    return list(entries)


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


__all__ = ["DEFAULT_ENDPOINTS", "discover_local", "probe_endpoint", "reset_cache"]
