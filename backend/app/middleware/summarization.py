import hashlib
import json
import logging
import time as tmod
from typing import Any

import litellm

from app.context.token_counter import estimate_tokens, estimate_tokens_messages
from app.monitor import record_model_call

logger = logging.getLogger(__name__)


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


def _chunk_cache_key(messages: list[dict]) -> str:
    """生成分块摘要的缓存键（基于 role+content 的规范化 JSON）。"""
    normalized = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = " ".join(text_parts)
        normalized.append({"role": role, "content": content})
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class HierarchicalSummarizationMiddleware:
    """Hierarchical conversation summarization middleware.

    When history exceeds the token trigger, older messages are split into
    chunks, each chunk is summarized independently, and the summaries are
    hierarchically merged until they fit within the budget. Recent messages
    are kept intact.

    优化点（相对旧版）：
    - 分块摘要按内容哈希缓存：长对话的旧分块跨请求不变，命中缓存后不再重复调用 LLM
    - 超长分块截断（保留头部 + 尾部），避免单次摘要请求塞入数万 token
    - 每次摘要 LLM 调用都写入本地监控统计

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
        cache_size: int = 200,
    ):
        """初始化分层摘要中间件，配置触发条件、保留策略和LLM参数。"""
        self.model = model
        self.trigger_key, self.trigger_value = trigger
        self.keep_key, self.keep_value = keep
        self.chunk_pairs = chunk_pairs
        self.api_key = api_key
        self.api_base = api_base
        self._cache: dict[str, str] = {}
        self._cache_size = cache_size

    def _cache_get(self, key: str):
        return self._cache.get(key)

    def _cache_set(self, key: str, value: str):
        if len(self._cache) >= self._cache_size:
            # 简单淘汰：清掉一半最旧的
            keys = list(self._cache.keys())
            for k in keys[: len(keys) // 2]:
                self._cache.pop(k, None)
        self._cache[key] = value

    async def apply(self, history: list[dict]) -> list[dict]:
        """对对话历史执行分层摘要压缩，超出触发阈值时压缩旧消息并保留最近消息。"""
        if not history:
            return history

        if self.trigger_key == "tokens" and estimate_tokens_messages(history) <= self.trigger_value:
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
        """递归分层摘要：将消息分块摘要，若摘要仍超预算则继续递归压缩。"""
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
        if estimate_tokens(combined) <= self.trigger_value:
            return combined

        # Recursively compress
        summary_messages = [{"role": "assistant", "content": s} for s in summaries]
        return await self._hierarchical_summarize(summary_messages, depth + 1)

    async def _summarize(self, messages: list[dict]) -> str:
        """调用LLM对一批消息生成简洁摘要，保留关键事实和决策。

        带内容哈希缓存：相同分块（跨请求不变的旧历史）直接命中缓存，
        不再重复调用 LLM。
        """
        cache_key = _chunk_cache_key(messages)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("summarization cache hit (chunk=%d msgs)", len(messages))
            return cached

        text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)

        # 超长分块截断：保留头部（目标）与尾部（最近进展），避免单次请求塞入数万 token
        max_input_tokens = 12_000
        if estimate_tokens(text) > max_input_tokens:
            chars = len(text)
            head = text[: chars // 3]
            tail = text[-chars // 3:] if chars // 3 else ""
            text = f"{head}\n\n... [middle portion omitted] ...\n\n{tail}"

        prompt = (
            "Condense the following conversation into a concise summary "
            "that preserves all key facts, decisions, and context. "
            "Write in the same language as the conversation.\n\n"
            f"{text}"
        )
        start = tmod.time()
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
            dur = (tmod.time() - start) * 1000
            usage = getattr(resp, "usage", None)
            pt = getattr(usage, "prompt_tokens", 0) if usage else 0
            ct = getattr(usage, "completion_tokens", 0) if usage else 0
            record_model_call(self.model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
            summary = resp.choices[0].message.content or ""
            if summary:
                self._cache_set(cache_key, summary)
            return summary
        except Exception as e:
            dur = (tmod.time() - start) * 1000
            record_model_call(self.model, duration_ms=dur)
            logger.warning("summarization failed: %s", e)
            return ""

    def _fallback_truncate(self, history: list[dict], max_tokens: int = 4000) -> list[dict]:
        """摘要失败时的兜底策略：从最新消息向前保留，截断超出token预算的旧消息。"""
        total = 0
        result = []
        for msg in reversed(history):
            tokens = estimate_tokens(msg.get("content", ""))
            if total + tokens > max_tokens:
                result.insert(0, {"role": "system", "content": "[earlier history truncated]"})
                break
            total += tokens
            result.insert(0, msg)
        return result
