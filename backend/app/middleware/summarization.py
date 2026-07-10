import logging
from typing import Any

import litellm

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    return len(text) // 2


def _total_tokens(messages: list[dict]) -> int:
    return sum(_estimate_tokens(m.get("content", "")) for m in messages)


def _split_into_chunks(messages: list[dict], pairs_per_chunk: int = 10) -> list[list[dict]]:
    """Split messages into chunks of conversation turns (user+assistant pairs)."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    pair_count = 0
    for m in messages:
        current.append(m)
        if m.get("role") == "assistant":
            pair_count += 1
            if pair_count >= pairs_per_chunk:
                chunks.append(current)
                current = []
                pair_count = 0
    if current:
        chunks.append(current)
    return chunks


class HierarchicalSummarizationMiddleware:
    """Hierarchical conversation summarization middleware.

    When history exceeds the token trigger, older messages are split into
    chunks, each chunk is summarized independently, and the summaries are
    hierarchically merged until they fit within the budget. Recent messages
    are kept intact.

    Signature:
        HierarchicalSummarizationMiddleware(
            model="gpt-4o-mini",
            trigger=("tokens", 4000),
            keep=("messages", 20),
            chunk_pairs=10,
        )
    """

    def __init__(
        self,
        model: str,
        trigger: tuple[str, int] = ("tokens", 4000),
        keep: tuple[str, int] = ("messages", 20),
        chunk_pairs: int = 10,
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        self.model = model
        self.trigger_key, self.trigger_value = trigger
        self.keep_key, self.keep_value = keep
        self.chunk_pairs = chunk_pairs
        self.api_key = api_key
        self.api_base = api_base

    async def apply(self, history: list[dict]) -> list[dict]:
        if not history:
            return history

        if self.trigger_key == "tokens" and _total_tokens(history) <= self.trigger_value:
            return history
        if self.trigger_key == "messages" and len(history) <= self.trigger_value:
            return history

        keep_count = min(self.keep_value, len(history) // 2)
        recent = history[-keep_count:]
        older = history[:-keep_count]

        summary = await self._hierarchical_summarize(older)
        if not summary:
            return self._fallback_truncate(history)

        return [
            {"role": "system", "content": f"[Conversation summary]: {summary}"},
            *recent,
        ]

    async def _hierarchical_summarize(self, messages: list[dict], depth: int = 0) -> str:
        if depth > 5:
            return ""

        chunks = _split_into_chunks(messages, self.chunk_pairs)
        if len(chunks) == 1:
            return await self._summarize(chunks[0])

        summaries: list[str] = []
        for chunk in chunks:
            s = await self._summarize(chunk)
            if not s:
                return ""
            summaries.append(s)

        # Check if combined summaries fit within token budget
        combined = "\n\n".join(summaries)
        if _estimate_tokens(combined) <= self.trigger_value:
            return combined

        # Recursively compress
        summary_messages = [{"role": "assistant", "content": s} for s in summaries]
        return await self._hierarchical_summarize(summary_messages, depth + 1)

    async def _summarize(self, messages: list[dict]) -> str:
        text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        prompt = (
            "Condense the following conversation into a concise summary "
            "that preserves all key facts, decisions, and context. "
            "Write in the same language as the conversation.\n\n"
            f"{text}"
        )
        try:
            kwargs = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 1024,
                "timeout": 30,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base
            resp = await litellm.acompletion(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("summarization failed: %s", e)
            return ""

    def _fallback_truncate(self, history: list[dict], max_tokens: int = 4000) -> list[dict]:
        total = 0
        result = []
        for msg in reversed(history):
            tokens = _estimate_tokens(msg.get("content", ""))
            if total + tokens > max_tokens:
                result.insert(0, {"role": "system", "content": "[earlier history truncated]"})
                break
            total += tokens
            result.insert(0, msg)
        return result
