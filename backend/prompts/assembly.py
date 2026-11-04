"""Stable-head prompt assembly.

The provider prompt cache keys on an exact byte prefix. Keep the role/profile/
shared head identical for a given (agent, model), and append turn-specific
content (vision hint, memory, RAG, skill catalog) after it. Tool definitions
stay outside this module and are ordered deterministically by the registry.
"""

from __future__ import annotations

from .loader import PromptLoader

_LOADER = PromptLoader()


def stable_head(agent_type: str, model_id: str | None = None) -> str:
    """Load the cacheable head: shared rules + provider profile + role."""
    return _LOADER.build_agent_prompt(agent_type, model_id)


def compose_system(
    agent_type: str,
    model_id: str | None = None,
    *,
    head_override: str | None = None,
    vision_prefix: str = "",
    memory: str = "",
    rag: str = "",
    extra: str = "",
) -> str:
    """Compose a system prompt with a stable head and volatile tail.

    ``head_override`` keeps explicit test/caller prompts unwrapped; everything
    after the head is turn-specific and may change between turns.
    """
    head = head_override if head_override is not None else stable_head(agent_type, model_id)
    parts: list[str] = []
    if head:
        parts.append(head)
    if vision_prefix:
        parts.append(vision_prefix)
    if memory:
        parts.append(f"[相关长期记忆]\n{memory}")
    if rag:
        parts.append(f"[知识库检索结果]\n{rag}")
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


__all__ = ["compose_system", "stable_head"]
