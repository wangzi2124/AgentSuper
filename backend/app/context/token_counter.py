"""Token counting module using tiktoken with fallback heuristics.

Provides accurate token estimation for LLM context window management.
Falls back to character-based heuristic when tiktoken is unavailable.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_encoder = None


def _get_encoder():
    """Lazy-load tiktoken encoder (cl100k_base encoding, used by GPT-3.5/4 and most models)."""
    global _encoder
    if _encoder is not None:
        return _encoder
    try:
        import tiktoken
        _encoder = tiktoken.get_encoding("cl100k_base")
        logger.debug("tiktoken encoder loaded: cl100k_base")
    except Exception as e:
        logger.warning("tiktoken unavailable, falling back to heuristic: %s", e)
        _encoder = False
    return _encoder


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string.

    Uses tiktoken when available (accurate), falls back to len(text) // 4
    heuristic (conservative for English, reasonable for Chinese).
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc and enc is not False:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback: ~4 chars per token (conservative for English)
    return max(1, len(text) // 4)


def estimate_tokens_messages(messages: list[dict]) -> int:
    """Estimate total tokens across a list of messages.

    Counts message content plus per-message overhead (role, formatting).
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multimodal: sum text parts
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
        elif isinstance(content, str):
            total += estimate_tokens(content)
        # Per-message overhead: ~4 tokens (role framing, separators)
        total += 4
    return total


def count_message_tokens(messages: list[dict]) -> int:
    """Count total tokens in a message list (alias for estimate_tokens_messages)."""
    return estimate_tokens_messages(messages)


def truncate_messages(
    messages: list[dict],
    max_tokens: int = 1_000_000,
    reserve_tokens: int = 4096,
) -> list[dict]:
    """Truncate message list to fit within token budget.

    Preserves system message (index 0) and most recent messages.
    Inserts a sentinel when truncation occurs.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        max_tokens: Maximum token budget for the entire message list.
        reserve_tokens: Tokens reserved for the model's response.

    Returns:
        Truncated message list with system message preserved.
    """
    if not messages:
        return messages

    total = estimate_tokens_messages(messages)
    if total <= max_tokens:
        return messages

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system_msg else messages

    system_tokens = estimate_tokens(system_msg.get("content", "")) + 4 if system_msg else 0
    budget = max_tokens - system_tokens - reserve_tokens

    kept = []
    current = 0
    for msg in reversed(rest):
        msg_tokens = estimate_tokens(msg.get("content", "")) + 4
        if current + msg_tokens > budget:
            break
        current += msg_tokens
        kept.append(msg)
    kept.reverse()

    result = []
    if system_msg:
        result.append(system_msg)
    if len(kept) < len(rest):
        result.append({
            "role": "system",
            "content": "[earlier messages truncated to fit context window]",
        })
    result.extend(kept)
    return result


def sanitize_tool_messages(messages: list[dict]) -> list[dict]:
    """Remove messages that violate OpenAI/DeepSeek tool-call ordering rules.

    Truncation or compaction can split a contiguous tool round
    [assistant-with-tool_calls, tool, tool, ...] in half, leaving 'tool'
    messages that are not a response to any preceding 'tool_calls'. APIs reject
    such lists with:
    "Messages with role 'tool' must be a response to a preceding message with
    'tool_calls'".

    Only complete, contiguous tool rounds are kept; orphaned tool messages and
    assistant messages whose tool responses were lost are dropped.
    """
    if not messages:
        return messages

    # Pass 1: drop orphaned tool messages (no contiguous matching assistant).
    kept: list[dict] = []
    for msg in messages:
        if msg.get("role") == "tool":
            prev = kept[-1] if kept else None
            if (
                prev is not None
                and prev.get("role") == "assistant"
                and prev.get("tool_calls")
                and msg.get("tool_call_id") in {tc.get("id") for tc in prev["tool_calls"]}
            ):
                kept.append(msg)
            else:
                logger.warning("Dropping orphaned tool message (tool_call_id=%s)", msg.get("tool_call_id"))
            continue
        kept.append(msg)

    # Pass 2: drop incomplete tool rounds (assistant-with-tool_calls whose
    # responses were truncated away along with their messages).
    result: list[dict] = []
    i = 0
    n = len(kept)
    while i < n:
        msg = kept[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            ids = {tc.get("id") for tc in msg["tool_calls"]}
            j = i + 1
            answered: set = set()
            while j < n and kept[j].get("role") == "tool":
                answered.add(kept[j].get("tool_call_id"))
                j += 1
            if not ids.issubset(answered):
                logger.warning(
                    "Dropping incomplete tool round (assistant missing responses: %s)", ids - answered
                )
                i = j
                continue
            result.extend(kept[i:j])
            i = j
            continue
        result.append(msg)
        i += 1
    return result
