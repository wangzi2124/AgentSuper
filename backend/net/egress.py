"""Unified egress factory: proxies, CA bundle and timeouts (opencode gap G-12).

All outbound HTTP/HTTPX call sites should build clients here so proxy, CA and
timeout policy stay in one place. SSRF validation remains the caller's job.
"""

from __future__ import annotations

import logging
import os
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20.0


@dataclass(frozen=True)
class EgressConfig:
    proxy: str = ""
    no_proxy: str = ""
    ca_bundle: str = ""
    verify: bool = True
    timeout: float = DEFAULT_TIMEOUT


def egress_config() -> EgressConfig:
    from config import get_settings

    proxy = str(getattr(get_settings(), "egress_proxy", "") or "").strip()
    if not proxy:
        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or ""
        )
    no_proxy = str(getattr(get_settings(), "egress_no_proxy", "") or "").strip()
    if not no_proxy:
        no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    ca_bundle = str(getattr(get_settings(), "egress_ca_bundle", "") or "").strip()
    if not ca_bundle:
        ca_bundle = (
            os.environ.get("SSL_CERT_FILE")
            or os.environ.get("REQUESTS_CA_BUNDLE")
            or ""
        )
    try:
        timeout = float(getattr(get_settings(), "egress_timeout_seconds", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return EgressConfig(
        proxy=proxy,
        no_proxy=no_proxy,
        ca_bundle=ca_bundle,
        verify=bool(getattr(get_settings(), "egress_verify", True)),
        timeout=max(1.0, timeout),
    )


def ssl_context(config: EgressConfig | None = None) -> ssl.SSLContext:
    config = config or egress_config()
    if not config.verify:
        logger.warning("egress TLS verification disabled by configuration")
        context = ssl._create_unverified_context()  # noqa: SLF001
    else:
        context = ssl.create_default_context()
    if config.ca_bundle:
        path = Path(config.ca_bundle).expanduser()
        try:
            context.load_verify_locations(cafile=str(path))
        except (OSError, ssl.SSLError) as exc:
            logger.warning("cannot load egress CA bundle %s: %s", path, exc)
    return context


def urllib_opener(config: EgressConfig | None = None) -> urllib.request.OpenerDirector:
    config = config or egress_config()
    handlers: list[Any] = [urllib.request.HTTPSHandler(context=ssl_context(config))]
    if config.proxy:
        handlers.append(
            urllib.request.ProxyHandler(
                {"http": config.proxy, "https": config.proxy}
            )
        )
    else:
        handlers.append(urllib.request.ProxyHandler())
    return urllib.request.build_opener(*handlers)


def httpx_client_kwargs(config: EgressConfig | None = None) -> dict[str, Any]:
    config = config or egress_config()
    kwargs: dict[str, Any] = {
        "timeout": config.timeout,
        "verify": ssl_context(config) if config.verify else False,
        "trust_env": not bool(config.proxy),
    }
    if config.proxy:
        kwargs["proxy"] = config.proxy
    return kwargs


def diagnose(url: str) -> dict[str, Any]:
    """Perform one short GET to verify proxy/CA/DNS connectivity."""
    from utils.ssrf import validate_http_url

    problem = validate_http_url(url)
    if problem:
        return {"ok": False, "error": problem}
    config = egress_config()
    try:
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "AgentSuper/diagnose"})
        with urllib_opener(config).open(request, timeout=min(5.0, config.timeout)) as response:
            return {
                "ok": True,
                "status": getattr(response, "status", 0),
                "proxy": bool(config.proxy),
                "ca_bundle": bool(config.ca_bundle),
                "timeout": config.timeout,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc) or repr(exc), "proxy": bool(config.proxy)}


__all__ = [
    "DEFAULT_TIMEOUT",
    "EgressConfig",
    "diagnose",
    "egress_config",
    "httpx_client_kwargs",
    "ssl_context",
    "urllib_opener",
]
