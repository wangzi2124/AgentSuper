"""Dedicated, tool-free compression agent.

The agent owns the compression system prompt and the model call; the
``ContextCompactor`` seam still owns planning, tail selection and fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_AGENT_TYPE = "compression_agent"

_FALLBACK_PROMPT = (
    "You are a context compression agent. Compress the older conversation "
    "into the structured summary requested in the task message. Preserve "
    "paths, commands, numbers and identifiers verbatim. Do not call tools."
)


@dataclass
class CompressionAgent:
    model: Any | None = None
    agent_type: str = DEFAULT_AGENT_TYPE
    _system_prompt: str | None = field(default=None, init=False, repr=False)

    def system_prompt(self) -> str:
        if self._system_prompt is None:
            try:
                from prompts.loader import build_agent_prompt, model_id_of

                self._system_prompt = build_agent_prompt(
                    self.agent_type, model_id_of(self.model)
                )
            except Exception as exc:  # noqa: BLE001 - prompt failure must not block
                logger.warning("compression agent prompt failed: %s", exc)
                self._system_prompt = _FALLBACK_PROMPT
        return self._system_prompt

    async def summarize(self, existing_summary: str, serialized: str) -> str:
        from cognition.compressor import build_compress_prompt

        model = self.model
        if model is None:
            from models.router import get_model

            model = get_model("simple")
            self.model = model
        prompt = build_compress_prompt(existing_summary, serialized)
        result = await model.ainvoke(
            [
                {"role": "system", "content": self.system_prompt()},
                {"role": "user", "content": prompt},
            ]
        )
        return str(getattr(result, "content", "") or "").strip()


__all__ = ["CompressionAgent", "DEFAULT_AGENT_TYPE"]
