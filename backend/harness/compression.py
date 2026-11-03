"""Unified compression helpers for graph compaction, manual compact and switch.

Aligns with opencode compaction:
- ``usable = context_window - reserved`` (reserved defaults to min(20k, max output));
- tail is chosen by user turns within ``preserve_recent_tokens``;
- summary uses the 8-section template, with a deterministic fallback when the
  LLM gate rejects the call or the model fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

COMPACTION_BUFFER = 20_000
MIN_PRESERVE_RECENT_TOKENS = 2_000
MAX_PRESERVE_RECENT_TOKENS = 8_000
DEFAULT_TAIL_TURNS = 2


class CompressionError(RuntimeError):
    """Compression could not preserve required facts."""


def _reserved_default(max_output_tokens: int = 0, window: int = 0) -> int:
    try:
        from config import settings

        configured = int(getattr(settings, "compaction_reserved_tokens", 0) or 0)
        global_max = int(getattr(settings, "llm_max_tokens", 32_000) or 32_000)
    except Exception:  # noqa: BLE001
        configured, global_max = 0, 32_000
    output = int(max_output_tokens or 0)
    if not output:
        # 目录未声明输出上限时，至少给窗口留一半给提示词，避免小窗口直接压到 0。
        output = min(global_max, max(512, int(window) // 2)) if window > 0 else global_max
    return configured or min(COMPACTION_BUFFER, output)


def _tail_turns_default(tail_turns: int | None = None) -> int:
    if tail_turns is not None:
        return max(0, int(tail_turns))
    try:
        from config import settings

        return max(0, int(getattr(settings, "context_tail_turns", DEFAULT_TAIL_TURNS) or DEFAULT_TAIL_TURNS))
    except Exception:  # noqa: BLE001
        return DEFAULT_TAIL_TURNS


def _preserve_default(usable: int, preserve: int | None = None) -> int:
    if preserve is not None and preserve > 0:
        return preserve
    try:
        from config import settings

        configured = int(getattr(settings, "compaction_preserve_recent_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        configured = 0
    if configured > 0:
        return configured
    return max(
        MIN_PRESERVE_RECENT_TOKENS,
        min(MAX_PRESERVE_RECENT_TOKENS, int(usable * 0.25)),
    )


@dataclass(frozen=True)
class CompressionPlan:
    threshold: int
    keep_tokens: int
    tail_turns: int = DEFAULT_TAIL_TURNS

    @classmethod
    def for_window(
        cls,
        window: int,
        *,
        default_threshold: int = 80_000,
        default_keep: int = 20_000,
        reserved: int = 0,
        preserve_recent_tokens: int = 0,
        tail_turns: int | None = None,
        max_output_tokens: int = 0,
    ) -> "CompressionPlan":
        turns = _tail_turns_default(tail_turns)
        if window <= 0:
            keep = preserve_recent_tokens or default_keep
            return cls(default_threshold, keep, turns)
        usable = max(
            2_000,
            int(window) - (reserved or _reserved_default(max_output_tokens, int(window))),
        )
        return cls(
            usable,
            _preserve_default(usable, preserve_recent_tokens),
            turns,
        )


@dataclass
class CompressionResult:
    summary: str = ""
    recent: list[Any] = field(default_factory=list)
    triggered: bool = False
    tokens: int = 0
    threshold: int = 0
    keep_tokens: int = 0
    tail_start_id: str | None = None
    missing_precision: list[str] = field(default_factory=list)
    compress_count: int = 0


def estimate_messages(messages: Sequence[Any]) -> int:
    from cognition.compressor import estimate_tokens, serialize_messages

    return sum(estimate_tokens(serialize_messages([m])) for m in messages)


def tail_start_id(recent: Sequence[Any]) -> str | None:
    if not recent:
        return None
    value = getattr(recent[0], "id", None)
    if value:
        return str(value)
    return None


def tail_snapshot(recent: Sequence[Any]) -> dict[str, Any]:
    """Return the epoch snapshot fields for the preserved recent tail."""
    if not recent:
        return {}
    first = recent[0]
    ident = getattr(first, "id", None)
    seq = getattr(first, "seq", None)
    out: dict[str, Any] = {}
    if ident:
        out["tail_start_id"] = ident
    if isinstance(seq, int):
        out["tail_start_seq"] = seq
    return out


def _role(msg: Any) -> str:
    if isinstance(msg, dict):
        value = msg.get("role") or msg.get("type") or ""
    else:
        value = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
    return "user" if value in ("human", "user") else ("assistant" if value in ("ai", "assistant") else str(value))


def turn_ranges(messages: Sequence[Any]) -> list[tuple[int, int]]:
    starts = [i for i, m in enumerate(messages) if _role(m) == "user"]
    if not starts:
        return []
    ends = starts[1:] + [len(messages)]
    return list(zip(starts, ends))


def select_tail_messages(
    messages: Sequence[Any],
    keep_tokens: int,
    tail_turns: int,
) -> tuple[list[Any], list[Any]]:
    """Split (head, tail) by user turns within ``keep_tokens``.

    Mirrors opencode's tail selection: keep whole recent turns; split the last
    overflowing turn at a message boundary. A split may start on an assistant
    message, but never on an orphan tool result.
    """
    msgs = list(messages)
    if not msgs:
        return [], []
    turns = turn_ranges(msgs)
    if not turns or tail_turns <= 0:
        return msgs, []

    keep: int | None = None
    total = 0
    for start, end in reversed(turns[-tail_turns:]):
        size = estimate_messages(msgs[start:end])
        if total + size <= keep_tokens:
            total += size
            keep = start
            continue
        remaining = max(0, keep_tokens - total)
        split: int | None = None
        for s in range(start + 1, end):
            if estimate_messages(msgs[s:end]) <= remaining:
                split = s
                break
        if split is not None:
            keep = split
        break

    if keep is None:
        last_user = turns[-1][0]
        if last_user == 0:
            return msgs, []
        return msgs[:last_user], msgs[last_user:]
    if keep == 0:
        return [], msgs

    tail = msgs[keep:]
    # 分割点可能落在 assistant 上（opencode 同样允许）；只避免 tail 以孤立 tool 结果开头。
    if tail and _role(tail[0]) == "tool" and keep > 0 and _role(msgs[keep - 1]) == "assistant":
        tail = [msgs[keep - 1]] + tail
    return msgs[: len(msgs) - len(tail)], tail


async def summarize_messages(
    messages: Sequence[Any],
    existing_summary: str = "",
    model: Any | None = None,
    *,
    segment_tokens: int = 0,
    raise_on_error: bool = False,
) -> str:
    """Summarize an explicit message slice (no splitting decision here)."""
    from cognition.compressor import (
        build_compress_prompt,
        estimate_tokens,
        serialize_messages,
    )

    msgs = list(messages)
    if not msgs:
        return existing_summary
    if model is None:
        from models.router import get_model

        model = get_model("simple")

    chunks: list[list[Any]] = [msgs]
    if segment_tokens > 0 and estimate_tokens(serialize_messages(msgs)) > segment_tokens:
        chunks = []
        current: list[Any] = []
        used = 0
        for msg in msgs:
            cost = estimate_tokens(serialize_messages([msg]))
            if current and used + cost > segment_tokens:
                chunks.append(current)
                current = []
                used = 0
            current.append(msg)
            used += cost
        if current:
            chunks.append(current)

    summary = existing_summary
    try:
        for chunk in chunks:
            prompt = build_compress_prompt(summary, serialize_messages(chunk))
            result = await model.ainvoke([{"role": "user", "content": prompt}])
            summary = str(getattr(result, "content", "") or "").strip() or summary
    except Exception as exc:  # noqa: BLE001
        if raise_on_error:
            raise CompressionError(f"摘要生成失败：{exc}") from exc
        logger.warning("compress LLM call failed, using fallback summary: %s", exc)
        from cognition.compressor import build_fallback_summary

        return build_fallback_summary(msgs, existing_summary)
    return summary


async def compress_messages(
    messages: Sequence[Any],
    *,
    existing_summary: str = "",
    model: Any | None = None,
    threshold: int = 80_000,
    keep_tokens: int = 20_000,
    tail_turns: int | None = None,
    segment_tokens: int = 0,
    require_precision: bool = False,
    raise_on_error: bool = False,
    require_llm: bool = False,
) -> CompressionResult:
    """Compress a history (switch/manual path) with turn-based tail selection."""
    from cognition.compressor import (
        build_fallback_summary,
        enforce_precision,
        extract_precision_values,
        serialize_messages,
        should_use_llm_summary,
    )

    msgs = list(messages)
    total = estimate_messages(msgs)
    if total <= threshold:
        return CompressionResult(
            summary=existing_summary,
            recent=msgs,
            triggered=False,
            tokens=total,
            threshold=threshold,
            keep_tokens=keep_tokens,
        )

    older, recent = select_tail_messages(msgs, keep_tokens, _tail_turns_default(tail_turns))
    if not older:
        return CompressionResult(
            summary=existing_summary,
            recent=msgs,
            triggered=False,
            tokens=total,
            threshold=threshold,
            keep_tokens=keep_tokens,
        )

    summary = existing_summary
    use_llm = require_llm or (model is not None and should_use_llm_summary(older))
    if use_llm:
        summary = await summarize_messages(
            older,
            existing_summary,
            model,
            segment_tokens=segment_tokens,
            raise_on_error=raise_on_error,
        )
    if not str(summary or "").strip():
        summary = build_fallback_summary(older, existing_summary)

    summary = enforce_precision(summary, older)
    preserved = f"{summary}\n{serialize_messages(recent)}"
    facts = extract_precision_values(serialize_messages(msgs))
    missing = [fact for fact in facts if fact not in preserved]
    if missing and require_precision:
        raise CompressionError("压缩结果缺失精确值：" + ", ".join(missing[:5]))

    return CompressionResult(
        summary=summary,
        recent=recent,
        triggered=True,
        tokens=total,
        threshold=threshold,
        keep_tokens=keep_tokens,
        tail_start_id=tail_start_id(recent),
        missing_precision=missing,
    )


async def compress_graph_messages(
    messages: Sequence[Any],
    *,
    existing_summary: str = "",
    model: Any | None = None,
    compress_count: int = 0,
    keep_recent: int = 6,
    keep_tokens: int = 0,
    tail_turns: int | None = None,
    require_llm: bool = False,
) -> CompressionResult:
    """Graph-node compression with turn-based tail + deterministic fallback."""
    from cognition.compressor import (
        build_fallback_summary,
        enforce_precision,
        should_use_llm_summary,
    )

    msgs = list(messages)
    total = estimate_messages(msgs)
    use_turns = tail_turns is not None or keep_tokens > 0
    if use_turns:
        older, recent = select_tail_messages(
            msgs, keep_tokens or 8_000, _tail_turns_default(tail_turns)
        )
    else:
        older, recent = msgs[:-keep_recent], msgs[-keep_recent:]
    if not older:
        return CompressionResult(
            summary=existing_summary,
            recent=msgs,
            triggered=False,
            tokens=total,
            compress_count=compress_count,
        )

    count = int(compress_count or 0)
    summary = existing_summary
    use_llm = require_llm or (model is not None and should_use_llm_summary(older, compress_count=count))
    if use_llm:
        summary = await summarize_messages(older, existing_summary, model)
        count += 1
    if not str(summary or "").strip():
        summary = build_fallback_summary(older, existing_summary)
    summary = enforce_precision(summary, older)
    return CompressionResult(
        summary=summary,
        recent=recent,
        triggered=True,
        tokens=total,
        keep_tokens=keep_tokens,
        tail_start_id=tail_start_id(recent),
        compress_count=count,
    )


__all__ = [
    "CompressionError",
    "CompressionPlan",
    "CompressionResult",
    "compress_graph_messages",
    "compress_messages",
    "estimate_messages",
    "select_tail_messages",
    "summarize_messages",
    "tail_snapshot",
    "tail_start_id",
    "turn_ranges",
]
