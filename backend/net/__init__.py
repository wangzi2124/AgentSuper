"""Network egress helpers (opencode gap G-12)."""

from .egress import (
    DEFAULT_TIMEOUT,
    EgressConfig,
    diagnose,
    egress_config,
    httpx_client_kwargs,
    ssl_context,
    urllib_opener,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "EgressConfig",
    "diagnose",
    "egress_config",
    "httpx_client_kwargs",
    "ssl_context",
    "urllib_opener",
]
