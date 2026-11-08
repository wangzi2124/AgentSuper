"""Graph role agents: supervisor/plan/rag/compress overrides (G-05)."""

from __future__ import annotations

import logging
from typing import Any

from .registry import AgentDef, load_registry

logger = logging.getLogger(__name__)

ROLE_AGENT_IDS = {
    "supervisor": "role_supervisor",
    "plan": "role_plan",
    "rag": "role_rag",
    "compress": "role_compress",
}


def role_agent(role: str, workspace: str | None = None) -> AgentDef | None:
    name = ROLE_AGENT_IDS.get(role)
    if not name:
        return None
    try:
        return load_registry(workspace).get(name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("role agent %s unavailable: %s", role, exc)
        return None


def role_prompt(role: str, default: str = "", workspace: str | None = None) -> str:
    agent = role_agent(role, workspace)
    if agent is None:
        return default
    if agent.prompt_body.strip():
        return agent.prompt_body.strip()
    if agent.prompt_path:
        try:
            return agent.system_prompt()
        except Exception:  # noqa: BLE001
            return default
    return default


def role_model(role: str, workspace: str | None = None) -> str:
    agent = role_agent(role, workspace)
    return agent.model if agent is not None else ""


def apply_role_model(role: str, model: Any, workspace: str | None = None) -> Any:
    """Wrap a model with the role agent's explicit model id when configured."""
    model_id = role_model(role, workspace)
    if not model_id:
        return model
    try:
        from models.router import TierModel

        return TierModel(tier="auto", model_override=model_id)
    except Exception:  # noqa: BLE001
        return model


__all__ = [
    "ROLE_AGENT_IDS",
    "apply_role_model",
    "role_agent",
    "role_model",
    "role_prompt",
]
