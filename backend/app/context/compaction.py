"""Context compaction for long-running tasks.

When the message list grows too large, compaction summarizes older messages
into a structured checkpoint, preserving key information while freeing token
budget. Inspired by OpenCode's compaction strategy:

- Turn-based tail preservation: the last `tail_turns` user turns are kept
  verbatim within a token budget; overflow turns are split at round
  boundaries so only the most recent tool rounds stay in context.
- Anchored summary: if a previous checkpoint exists, the new summary updates
  it (preserve still-true details, drop stale ones, merge new facts) instead
  of rewriting from scratch.
- Fallback truncation when summarization fails.

Compaction produces a structured summary with:
- Task objective
- Important details / constraints
- Work state (completed / active / blocked)
- Next move
- Relevant files/context
"""

import logging
import time as tmod
from typing import Optional

import litellm

from app.context.token_counter import estimate_tokens, estimate_tokens_messages
from app.monitor import record_model_call

logger = logging.getLogger(__name__)

# Default threshold: trigger compaction when messages exceed this many tokens
DEFAULT_COMPACTION_THRESHOLD = 80_000

# How many recent user turns to keep intact (aligned with opencode tail_turns)
DEFAULT_TAIL_TURNS = 2

# Token budget for the preserved tail (aligned with opencode preserve_recent_tokens)
DEFAULT_PRESERVE_RECENT_TOKENS = 8_000

COMPACTION_MARKER = "[Task checkpoint"

# Anchored summary template, aligned with opencode core/session/compaction.ts
COMPACTION_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted.
- Write in the same language as the conversation."""

COMPACTION_MESSAGE_TEMPLATE = (
    "[Task checkpoint — conversation compacted to save context space]\n\n"
    "{summary}\n\n"
    "[Recent messages preserved below — do not repeat work already described above]"
)


def is_checkpoint(msg: dict) -> bool:
    """判断消息是否为之前压缩产生的 checkpoint（system 角色 + 标记前缀）。"""
    return bool(msg) and msg.get("role") == "system" and str(msg.get("content", "")).startswith(COMPACTION_MARKER)


def _summary_of(msg: dict) -> Optional[str]:
    """从 checkpoint 消息中提取上一份摘要正文。"""
    content = str(msg.get("content", ""))
    if "\n\n" not in content:
        return None
    summary = content.split("\n\n", 1)[1]
    marker = "\n\n[Recent messages preserved"
    if marker in summary:
        summary = summary.split(marker, 1)[0]
    summary = summary.strip()
    return summary or None


def previous_summary_of(messages: list[dict]) -> Optional[str]:
    """取最近一份已存在 checkpoint 的摘要作为锚定基准。"""
    for msg in reversed(messages):
        if is_checkpoint(msg):
            summary = _summary_of(msg)
            if summary:
                return summary
    return None


class ContextCompactor:
    """Compacts conversation context when token budget is exceeded.

    Usage:
        compactor = ContextCompactor(model="deepseek-chat", threshold=44_000)
        if compactor.should_compact(messages):
            messages = await compactor.compact(messages)
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        threshold: int = DEFAULT_COMPACTION_THRESHOLD,
        tail_turns: int = DEFAULT_TAIL_TURNS,
        preserve_recent_tokens: int = DEFAULT_PRESERVE_RECENT_TOKENS,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.threshold = threshold
        self.tail_turns = tail_turns
        self.preserve_recent_tokens = preserve_recent_tokens

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if messages exceed the compaction threshold."""
        total = estimate_tokens_messages(messages)
        return total > self.threshold

    def _select(self, messages: list[dict], budget: int) -> tuple[list[dict], list[dict]]:
        """把消息切成 (head, tail)：head 待压缩、tail 原样保留。

        - 以 user 消息划分轮次，默认保留最后 tail_turns 轮（受 budget 约束）。
        - 超预算的轮次在"轮次边界"（user 开头或完整工具轮起点）处切开，
          只保留最近部分，避免切断 tool_calls ↔ tool 的对应关系。
        - 分割后若尾部不以 user 开头，则把最新的 user 问题补到尾部开头，
          保证下一轮 LLM 调用仍有用户消息锚定。
        """
        if not messages:
            return messages, []

        user_idx = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if not user_idx:
            return messages, []

        boundaries = sorted({
            0,
            *user_idx,
            *(i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")),
        })
        n = len(user_idx)
        ends = user_idx[1:] + [len(messages)]

        total = 0
        keep = None
        for j in range(n - 1, -1, -1):
            if n - j > self.tail_turns:
                break
            start, end = user_idx[j], ends[j]
            size = estimate_tokens_messages(messages[start:end])
            if total + size <= budget:
                total += size
                keep = start
                continue
            remaining = max(0, budget - total)
            split = self._split_suffix(messages, start, end, boundaries, remaining)
            if split is not None:
                keep = split
            break

        if keep is None:
            # 预算完全放不下（含分割失败）→ 兜底把最新一整轮作为尾部，
            # 保证主循环仍锚定在最近的 user 消息上。
            if len(messages) <= 1:
                return messages, []
            last_user = user_idx[-1]
            if last_user == 0:
                return messages, []
            return messages[:last_user], messages[last_user:]

        if keep == 0:
            # 整个对话都在保留预算内 → 没有更旧的内容可压缩
            return [], messages

        tail = messages[keep:]
        # 分割路径的尾部不以 user 开头 → 补上最新的 user 问题（体积很小）
        if tail and tail[0].get("role") != "user":
            nu = user_idx[-1]
            if nu < keep:
                tail = [messages[nu]] + tail
        return messages[:keep], tail

    def _split_suffix(self, messages: list[dict], start: int, end: int,
                      boundaries: list[int], remaining: int) -> int | None:
        """在 (start, end) 内找最大的轮次边界 b，使 messages[b:end] 落在剩余预算内。"""
        for b in reversed(boundaries):
            if start < b < end:
                size = estimate_tokens_messages(messages[b:end])
                if size <= remaining:
                    return b
        return None

    async def compact(self, messages: list[dict]) -> list[dict]:
        """Compact messages by summarizing older messages into a checkpoint.

        Returns a new message list with:
        - System message(s) preserved (previous checkpoints replaced)
        - Compaction summary inserted (anchored on previous summary if any)
        - Recent turns kept intact
        """
        if not messages:
            return messages

        previous_summary = previous_summary_of(messages)
        system_msgs = [m for m in messages if m.get("role") == "system" and not is_checkpoint(m)]
        conversation = [m for m in messages if m.get("role") != "system"]

        if not conversation:
            return messages

        head, tail = self._select(conversation, self.preserve_recent_tokens)
        if not head:
            # 最新几轮已全在尾部保留，没有更旧的内容可压缩
            return messages

        # Generate anchored summary of the head
        summary = await self._summarize(head, previous_summary)
        if not summary:
            # Compaction failed, fall back to truncation
            logger.warning("Compaction summarization failed, falling back to truncation")
            return self._fallback_truncate(messages)

        # Build compacted message list
        checkpoint_content = COMPACTION_MESSAGE_TEMPLATE.format(summary=summary)
        checkpoint_msg = {"role": "system", "content": checkpoint_content}

        result = system_msgs + [checkpoint_msg] + tail

        # 压缩后仍可能超限（tail 本身很大，含近期超长工具输出）→ 校验并回溯清理
        result = self._ensure_within_budget(result)

        old_tokens = estimate_tokens_messages(head)
        new_tokens = estimate_tokens(checkpoint_content)
        logger.info(
            "Compaction: %d messages (%d tokens) -> checkpoint (%d tokens) + tail (%d messages, %d tokens)",
            len(head), old_tokens, new_tokens, len(tail), estimate_tokens_messages(tail),
        )

        return result

    def _ensure_within_budget(self, result: list[dict]) -> list[dict]:
        """压缩结果可能仍超限：与阈值比对，超限则回溯打桩旧工具输出。

        迭代收紧保护预算（protect_tokens）并重测；minimum_tokens 取溢出量的一半，
        避免"必须一轮回收全部溢出"才落桩（那会被尾部保护挡住而完全不回收）。
        若回收后仍超限（如 tail 保护轮内工具输出本身就很大）则告警而非静默返回。
        """
        from app.context.tool_output import prune_tool_outputs

        protect = self.preserve_recent_tokens
        for _ in range(4):
            tokens = estimate_tokens_messages(result)
            overflow = tokens - self.threshold
            if overflow <= 0:
                return result
            pruned = prune_tool_outputs(
                result,
                protect_tokens=max(0, protect),
                minimum_tokens=max(1, overflow // 2),
                tail_turns=self.tail_turns,
            )
            if pruned is result:
                break
            result = pruned
            protect = max(0, protect // 2)

        tokens = estimate_tokens_messages(result)
        if tokens > self.threshold:
            logger.warning(
                "Compacted context still exceeds threshold (%d > %d) after pruning; "
                "consider raising the compaction threshold or lowering the tail budget",
                tokens, self.threshold,
            )
        return result

    async def _summarize(self, messages: list[dict], previous_summary: str | None) -> str:
        """Call LLM to generate an anchored structured summary of older messages."""
        # Build conversation text
        lines = []
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, list):
                # Multimodal: extract text parts
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(text_parts)
            if content:
                lines.append(f"[{role}]: {content}")

        conversation_text = "\n".join(lines)

        # Truncate if conversation itself is too long
        max_input_tokens = 16_000
        if estimate_tokens(conversation_text) > max_input_tokens:
            # Keep first 20% (for objective) + last 20% (for recent work)
            chars = len(conversation_text)
            head = conversation_text[: chars // 5]
            tail = conversation_text[-chars // 5:] if chars // 5 else ""
            conversation_text = f"{head}\n\n... [middle portion omitted] ...\n\n{tail}"

        if previous_summary:
            instruction = (
                "Update the anchored summary below using the conversation history above.\n"
                "Preserve still-true details, remove stale details, and merge in the new facts.\n"
                f"<previous-summary>\n{previous_summary}\n</previous-summary>"
            )
        else:
            instruction = "Create a new anchored summary from the conversation history."

        prompt = (
            instruction
            + "\n\n"
            + COMPACTION_TEMPLATE
            + "\n\n---\nCONVERSATION HISTORY:\n"
            + conversation_text
        )

        try:
            kwargs = {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 2048,
                "timeout": 60,
            }
            if self.model:
                kwargs["model"] = self.model
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            start = tmod.time()
            resp = await litellm.acompletion(**kwargs)
            dur = (tmod.time() - start) * 1000
            usage = getattr(resp, "usage", None)
            pt = getattr(usage, "prompt_tokens", 0) if usage else 0
            ct = getattr(usage, "completion_tokens", 0) if usage else 0
            record_model_call(self.model or "compaction", prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("Compaction LLM call failed: %s", e)
            return ""

    def _fallback_truncate(self, messages: list[dict]) -> list[dict]:
        """Fallback: keep system messages + most recent messages."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        budget = self.threshold
        kept = []
        current = 0
        for msg in reversed(non_system):
            msg_tokens = estimate_tokens(msg.get("content", "")) + 4
            if current + msg_tokens > budget:
                break
            current += msg_tokens
            kept.append(msg)
        kept.reverse()

        sentinel = {"role": "system", "content": "[earlier messages truncated to fit context window]"}
        return system_msgs + [sentinel] + kept
