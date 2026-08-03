"""Smart tool output bounding module.

Applies line-count and byte-size limits to tool outputs before they enter
the LLM context window. Prevents large outputs (file reads, shell commands)
from dominating the context.

Inspired by OpenCode's pruning strategy:
- Bounded output with clear truncation indicators
- Preserves error context (stderr) even when stdout is truncated
- Different limits for different output types
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolOutputLimits:
    """Configurable limits for tool output bounding."""
    max_lines: int = 200
    max_bytes: int = 32_768  # 32 KB
    # Tools that should have tighter limits (e.g., grep can produce massive output)
    tight_limits: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "tool_grep": (100, 16_384),
        "tool_execute": (300, 32_768),
        "tool_read_file": (200, 32_768),
    })


_DEFAULT_LIMITS = ToolOutputLimits()


def bound_tool_output(
    output: str,
    tool_name: str = "",
    limits: ToolOutputLimits | None = None,
) -> str:
    """Apply smart bounding to tool output.

    Truncates output that exceeds line-count or byte-size limits.
    Preserves the beginning (most important) and appends a truncation notice.

    Args:
        output: Raw tool output string.
        tool_name: Name of the tool (for applying tool-specific limits).
        limits: Custom limits (uses defaults if None).

    Returns:
        Bounded output string, possibly truncated with a notice.
    """
    if not output:
        return output

    lim = limits or _DEFAULT_LIMITS
    max_lines, max_bytes = lim.max_lines, lim.max_bytes

    # Apply tool-specific tighter limits
    if tool_name in lim.tight_limits:
        max_lines, max_bytes = lim.tight_limits[tool_name]

    original_bytes = len(output.encode("utf-8"))
    original_lines = output.count("\n") + 1

    truncated = False
    truncated_lines = original_lines
    truncated_bytes = original_bytes

    # Step 1: Truncate by line count
    if original_lines > max_lines:
        lines = output.split("\n")
        output = "\n".join(lines[:max_lines])
        truncated_lines = max_lines
        truncated = True

    # Step 2: Truncate by byte size
    encoded = output.encode("utf-8")
    if len(encoded) > max_bytes:
        output = encoded[:max_bytes].decode("utf-8", errors="ignore")
        truncated_bytes = max_bytes
        truncated = True

    if truncated:
        notice = (
            f"\n\n[output truncated: showed {truncated_lines}/{original_lines} lines, "
            f"{truncated_bytes}/{original_bytes} bytes]"
        )
        output = output + notice
        logger.debug(
            "Tool output bounded: %s -> %d lines, %d bytes (from %d, %d)",
            tool_name, truncated_lines, truncated_bytes, original_lines, original_bytes,
        )

    return output


def estimate_output_tokens(output: str) -> int:
    """Quick token estimate for a tool output string."""
    from app.context.token_counter import estimate_tokens
    return estimate_tokens(output)


_COMPACTION_MARKER = "[Task checkpoint"
_PRUNE_STUB_PREFIX = "[tool output pruned"


def prune_tool_outputs(
    messages: list[dict],
    protect_tokens: int = 40_000,
    minimum_tokens: int = 20_000,
    tail_turns: int = 2,
) -> list[dict]:
    """回溯式清理已进入上下文的旧工具输出，腾出 token 预算。

    对齐 opencode `compaction.ts:prune`：
    - 从最新消息往回走，越过最近 tail_turns 个 user 轮次后才开始评估；
    - 已完成工具输出累计 token 超过 protect_tokens 后，更旧的被替换为桩；
    - 仅当清理总量 >= minimum_tokens 才真正落桩，避免微小收益的频繁改写；
    - 遇到更早的压缩 checkpoint（system 角色 + 标记）即停止扫描。

    与入口 bounding（`bound_tool_output`）互补：前者限制新输出大小，
    本函数回收存量输出。返回新列表；未达到最低收益时原样返回。
    """
    if not messages:
        return messages

    result = list(messages)
    user_count = 0
    total = 0
    pruned = 0
    candidates: list[int] = []

    for i in range(len(result) - 1, -1, -1):
        msg = result[i]
        role = msg.get("role")
        if role == "system" and str(msg.get("content", "")).startswith(_COMPACTION_MARKER):
            break
        if role == "user":
            user_count += 1
        if user_count < tail_turns:
            continue
        if role != "tool":
            continue
        content = msg.get("content") or ""
        if content.startswith(_PRUNE_STUB_PREFIX):
            continue
        tokens = estimate_output_tokens(content)
        total += tokens
        if total <= protect_tokens:
            continue
        pruned += tokens
        candidates.append(i)

    if not candidates or pruned < minimum_tokens:
        return messages

    for i in candidates:
        content = result[i].get("content") or ""
        omitted = estimate_output_tokens(content)
        result[i] = {
            **result[i],
            "content": f"[tool output pruned to save context space: ~{omitted} tokens omitted]",
        }
    logger.info("Pruned %d tool outputs (~%d tokens) to free context budget", len(candidates), pruned)
    return result
