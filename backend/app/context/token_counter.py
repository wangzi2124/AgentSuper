"""Token counting module using tiktoken with fallback heuristics.

Provides accurate token estimation for LLM context window management.
Falls back to character-based heuristic when tiktoken is unavailable.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_encoder = None
_correction = None


def _estimate_correction() -> float:
    """cl100k_base → DeepSeek tokenizer 的估算校正系数。

    [token 优化 P8] 实测 tiktoken cl100k_base 对 DeepSeek tokenizer 系统性低估
    ~13%（analyze_token_trace：round8 估算 20,851 vs 实际 23,599）。在
    estimate_tokens 一处校正，truncate_messages / compactor.should_compact 等
    所有下游判断自动随之修正。惰性求值避免模块加载顺序问题。
    """
    global _correction
    if _correction is None:
        try:
            from app.config import settings
            _correction = max(1.0, float(getattr(settings, "token_estimate_correction", 1.13)))
        except Exception:
            _correction = 1.13
    return _correction


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
    corr = _estimate_correction()
    if enc and enc is not False:
        try:
            return max(1, round(len(enc.encode(text)) * corr))
        except Exception:
            pass
    # Fallback: ~4 chars per token (conservative for English)
    return max(1, round(len(text) / 4 * corr))


def _estimate_single_message(msg: dict) -> int:
    """Estimate tokens of a single message including tool_calls and overhead."""
    content = msg.get("content", "")
    n = 0
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                n += estimate_tokens(part.get("text", ""))
    elif isinstance(content, str):
        n += estimate_tokens(content)
    tcs = msg.get("tool_calls") or []
    for tc in tcs:
        fn = tc.get("function") or {}
        n += estimate_tokens(fn.get("name", ""))
        n += estimate_tokens(fn.get("arguments", ""))
        n += 8
    return n + 4


def estimate_tokens_messages(messages: list[dict]) -> int:
    """Estimate total tokens across a list of messages.

    Counts message content plus per-message overhead (role, formatting).
    """
    return sum(_estimate_single_message(msg) for msg in messages)


def estimate_tools(tool_defs: list[dict] | None) -> int:
    """Estimate tokens of serialized OpenAI-style tool definitions (schema).

    DeepSeek 把本轮发送的 tools schema 序列化为 JSON 计入 prompt_tokens，
    而 estimate_tokens_messages 只统计消息体。truncate_messages 预算须减去该
    开销，否则截断后实际 pt 仍会越界。
    """
    if not tool_defs:
        return 0
    try:
        return estimate_tokens(json.dumps(tool_defs, ensure_ascii=False))
    except Exception:
        return 0


def count_message_tokens(messages: list[dict]) -> int:
    """Count total tokens in a message list (alias for estimate_tokens_messages)."""
    return estimate_tokens_messages(messages)


def truncate_messages(
    messages: list[dict],
    max_tokens: int = 1_000_000,
    reserve_tokens: int = 4096,
    tool_defs: list[dict] | None = None,
) -> list[dict]:
    """Truncate message list to fit within token budget.

    Preserves system message (index 0) and most recent messages.
    Inserts a sentinel when truncation occurs.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        max_tokens: Maximum token budget for the entire message list.
        reserve_tokens: Tokens reserved for the model's response.
        tool_defs: [token 优化 P9] 本轮将要随请求发送的 tools schema；
            其序列化 token 数从预算中扣除，保证截断后实际 pt 不越界。

    Returns:
        Truncated message list with system message preserved.
    """
    if not messages:
        return messages

    schema_tokens = estimate_tools(tool_defs)
    total = estimate_tokens_messages(messages) + schema_tokens
    if total <= max_tokens:
        return messages

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    rest = messages[1:] if system_msg else messages

    system_tokens = estimate_tokens(system_msg.get("content", "")) + 4 if system_msg else 0
    budget = max_tokens - system_tokens - reserve_tokens - schema_tokens

    kept = []
    current = 0
    for msg in reversed(rest):
        msg_tokens = _estimate_single_message(msg)
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
            # 校验目标是最近一条带 tool_calls 的 assistant（同一轮可能有
            # 多条 tool 结果，紧邻上一条可能是另一个 tool 消息）
            prev_assistant = None
            for k in range(len(kept) - 1, -1, -1):
                if kept[k].get("role") == "assistant" and kept[k].get("tool_calls"):
                    prev_assistant = kept[k]
                    break
            if (
                prev_assistant is not None
                and msg.get("tool_call_id") in {tc.get("id") for tc in prev_assistant["tool_calls"]}
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
