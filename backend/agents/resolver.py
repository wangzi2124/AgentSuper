"""Agent selection and validation (opencode gap G-05)."""

from __future__ import annotations

from .registry import AgentDef, AgentRegistry

FALLBACK_PRIMARY = "build"


class AgentResolver:
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def default_agent(self, requested: str | None = None) -> AgentDef | None:
        return self.primary(requested)

    def primary(self, requested: str | None = None) -> AgentDef | None:
        """Resolve a primary agent with a deterministic fallback chain."""
        if requested:
            agent = self.registry.get(requested)
            if agent is not None and agent.is_primary and not agent.disabled and not agent.hidden:
                return agent
        agent = self.registry.get(FALLBACK_PRIMARY)
        if agent is not None and agent.is_primary and not agent.disabled:
            return agent
        primaries = self.registry.list(mode="primary")
        return primaries[0] if primaries else None

    def subagent(self, requested: str) -> AgentDef | None:
        agent = self.registry.get(requested)
        if agent is None or not agent.is_subagent or agent.disabled:
            return None
        return agent


__all__ = ["AgentResolver", "FALLBACK_PRIMARY"]
