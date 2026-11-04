"""Provider prompt-cache hints.

Anthropic accepts explicit ``cache_control`` breakpoints. Place one at the end
of the stable system prefix and one at the latest user message: the user turn
stays fixed while a single turn expands into many assistant/tool rounds, so
every intra-turn call can reuse the cached prefix. Other providers use implicit
server-side caching and are left untouched.
"""

from __future__ import annotations

from typing import Any

_TEXT_TYPES = frozenset({"text", "input_text"})


def is_anthropic_model(model_id: str | None) -> bool:
    provider = str(model_id or "").strip().lower().split("/", 1)[0]
    return provider == "anthropic"


def _mark_content(content: Any) -> Any:
    if isinstance(content, str):
        if not content:
            return content
        return [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    if isinstance(content, list) and content:
        blocks = [dict(block) if isinstance(block, dict) else block for block in content]
        target = None
        for index in range(len(blocks) - 1, -1, -1):
            block = blocks[index]
            if isinstance(block, dict) and str(block.get("type") or "") in _TEXT_TYPES:
                target = index
                break
        if target is None:
            target = len(blocks) - 1
            if not isinstance(blocks[target], dict):
                return content
        blocks[target] = {
            **blocks[target],
            "cache_control": {"type": "ephemeral"},
        }
        return blocks
    return content


def apply_cache_hints(
    messages: list[dict[str, Any]], model_id: str | None
) -> list[dict[str, Any]]:
    """Add Anthropic cache breakpoints to the system and latest user messages."""
    if not messages or not is_anthropic_model(model_id):
        return messages
    system_index = -1
    user_index = -1
    for index, message in enumerate(messages):
        role = str(message.get("role") or "")
        if role == "system":
            system_index = index
        elif role == "user":
            user_index = index
    if system_index < 0 and user_index < 0:
        return messages
    out = [dict(message) for message in messages]
    for index in {system_index, user_index}:
        if index < 0:
            continue
        out[index]["content"] = _mark_content(out[index].get("content"))
    return out


__all__ = ["apply_cache_hints", "is_anthropic_model"]
