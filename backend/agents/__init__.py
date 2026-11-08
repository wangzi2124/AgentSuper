"""Agent registry package (opencode gap G-05)."""

from .registry import (
    AgentDef,
    AgentRegistry,
    VALID_MODES,
    delete_agent,
    load_registry,
    parse_agent_file,
    save_agent,
)
from .resolver import AgentResolver, FALLBACK_PRIMARY

__all__ = [
    "AgentDef",
    "AgentRegistry",
    "AgentResolver",
    "FALLBACK_PRIMARY",
    "VALID_MODES",
    "delete_agent",
    "load_registry",
    "parse_agent_file",
    "save_agent",
]
