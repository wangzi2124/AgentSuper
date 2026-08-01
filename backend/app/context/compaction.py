"""Context compaction for long-running tasks.

When the message list grows too large, compaction summarizes older messages
into a structured checkpoint, preserving key information while freeing token
budget. Inspired by OpenCode's compaction strategy.

Compaction produces a structured summary with:
- Task objective
- Completed work
- Current state
- Next steps
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

# How many recent messages to always keep intact
DEFAULT_KEEP_RECENT = 6

# Compaction summary prompt
COMPACTION_PROMPT = """You are a task checkpoint manager. Summarize the following conversation into a structured checkpoint that preserves all critical information for continuing the work.

Write the summary as a concise checkpoint with these sections:
- **Objective**: What the user asked for
- **Completed**: What has been done so far (with specific file paths, function names, decisions)
- **Current State**: Where the task currently stands
- **Next Steps**: What remains to be done
- **Key Context**: Important files, variables, patterns, or constraints

Be specific and precise. Include file paths, function names, variable names, and exact technical details. This summary will be used to continue the task without re-reading the original conversation.

Language: write in the same language as the conversation.

---
CONVERSATION TO SUMMARIZE:
"""

COMPACTION_MESSAGE_TEMPLATE = (
    "[Task checkpoint — conversation compacted to save context space]\n\n"
    "{summary}\n\n"
    "[Recent messages preserved below — do not repeat work already described above]"
)


class ContextCompactor:
    """Compacts conversation context when token budget is exceeded.

    Usage:
        compactor = ContextCompactor(model="deepseek-chat")
        if compactor.should_compact(messages):
            messages = await compactor.compact(messages)
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        threshold: int = DEFAULT_COMPACTION_THRESHOLD,
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.threshold = threshold
        self.keep_recent = keep_recent

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if messages exceed the compaction threshold."""
        total = estimate_tokens_messages(messages)
        return total > self.threshold

    async def compact(self, messages: list[dict]) -> list[dict]:
        """Compact messages by summarizing older messages into a checkpoint.

        Returns a new message list with:
        - System message(s) preserved
        - Compaction summary inserted
        - Recent messages kept intact
        """
        if not messages:
            return messages

        # Separate system messages from conversation messages
        system_msgs = [m for m in messages if m.get("role") == "system" and "[earlier messages truncated" not in m.get("content", "")]
        conversation = [m for m in messages if m.get("role") != "system" or "[earlier messages truncated" in m.get("content", "")]

        if len(conversation) <= self.keep_recent:
            return messages  # Nothing to compact

        # Split: older (to compact) + recent (to keep)
        older = conversation[:-self.keep_recent]
        recent = conversation[-self.keep_recent:]

        # Generate summary of older messages
        summary = await self._summarize(older)
        if not summary:
            # Compaction failed, fall back to truncation
            logger.warning("Compaction summarization failed, falling back to truncation")
            return self._fallback_truncate(messages)

        # Build compacted message list
        checkpoint_content = COMPACTION_MESSAGE_TEMPLATE.format(summary=summary)
        checkpoint_msg = {"role": "system", "content": checkpoint_content}

        result = system_msgs + [checkpoint_msg] + recent

        old_tokens = estimate_tokens_messages(older)
        new_tokens = estimate_tokens(checkpoint_content)
        logger.info(
            "Compaction: %d messages (%d tokens) -> checkpoint (%d tokens) + %d recent messages",
            len(older), old_tokens, new_tokens, len(recent),
        )

        return result

    async def _summarize(self, messages: list[dict]) -> str:
        """Call LLM to generate a structured summary of older messages."""
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
            # Keep first 20% (for objective) + last 80% (for recent work)
            chars = len(conversation_text)
            head = conversation_text[:chars // 5]
            tail = conversation_text[chars // 5:]
            conversation_text = f"{head}\n\n... [middle portion omitted] ...\n\n{tail}"

        prompt = COMPACTION_PROMPT + conversation_text

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
