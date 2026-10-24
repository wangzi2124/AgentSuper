"""Unified compression helpers for graph compaction, manual compact and switch.

This module owns the deterministic parts of context compression: token estimate,
window-derived thresholds, summary generation, precision backfill and tail anchor.
It does not touch storage; callers decide where the summary/watermark is written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)


class CompressionError(RuntimeError):
    """Compression could not preserve required facts."""


@dataclass(frozen=True)
class CompressionPlan:
    threshold: int
    keep_tokens: int

    @classmethod
    def for_window(cls, window: int, *, default_threshold: int = 80_000,
                   default_keep: int = 20_000) -> "CompressionPlan":
        if window <= 0:
            return cls(default_threshold, default_keep)
        return cls(
            threshold=max(2_000, int(window * 0.6)),
            keep_tokens=max(1_000, min(20_000, int(window * 0.25))),
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


async def summarize_messages(
    messages: Sequence[Any],
    existing_summary: str = "",
    model: Any | None = None,
    *,
    segment_tokens: int = 0,
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
    for chunk in chunks:
        prompt = build_compress_prompt(summary, serialize_messages(chunk))
        result = await model.ainvoke([{"role": "user", "content": prompt}])
        summary = str(getattr(result, "content", "") or "").strip() or summary
    return summary


async def compress_messages(
    messages: Sequence[Any],
    *,
    existing_summary: str = "",
    model: Any | None = None,
    threshold: int = 80_000,
    keep_tokens: int = 20_000,
    segment_tokens: int = 0,
    require_precision: bool = False,
    raise_on_error: bool = False,
) -> CompressionResult:
    """Compress a history via the canonical ``cognition.compressor`` core."""
    from cognition.compressor import (
        compress_context,
        enforce_precision,
        extract_precision_values,
        serialize_messages,
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

    try:
        result = await compress_context(
            msgs,
            existing_summary=existing_summary,
            model=model,
            threshold=threshold,
            keep_tokens=keep_tokens,
            raise_on_error=raise_on_error,
            segment_tokens=segment_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        if raise_on_error:
            raise CompressionError(f"???????{exc}") from exc
        logger.warning("compress LLM call failed, keeping existing summary: %s", exc)
        result = {
            "summary": existing_summary,
            "recent": msgs,
            "triggered": False,
            "tokens": total,
        }

    summary = str(result.get("summary") or existing_summary)
    recent = list(result.get("recent") or [])
    if not result.get("triggered"):
        return CompressionResult(
            summary=summary,
            recent=recent,
            triggered=False,
            tokens=int(result.get("tokens") or total),
            threshold=threshold,
            keep_tokens=keep_tokens,
        )

    recent_ids = {id(m) for m in recent}
    older = [m for m in msgs if id(m) not in recent_ids]
    summary = enforce_precision(summary, older)
    preserved = f"{summary}\n{serialize_messages(recent)}"
    facts = extract_precision_values(serialize_messages(msgs))
    missing = [fact for fact in facts if fact not in preserved]
    if missing and require_precision:
        raise CompressionError(
            "?????????????" + ", ".join(missing[:5])
        )

    return CompressionResult(
        summary=summary,
        recent=recent,
        triggered=True,
        tokens=int(result.get("tokens") or total),
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
) -> CompressionResult:
    """Graph-node compression: message-count tail + C1/C2/C3 gate.

    The graph historically keeps the last N messages verbatim (rather than a token
    budget), so this entry preserves that split while sharing summary/precision/tail
    with the switch path.
    """
    from cognition.compressor import (
        enforce_precision,
        should_use_llm_summary,
    )

    msgs = list(messages)
    total = estimate_messages(msgs)
    if len(msgs) <= keep_recent:
        return CompressionResult(
            summary=existing_summary,
            recent=msgs,
            triggered=False,
            tokens=total,
            compress_count=compress_count,
        )

    older = msgs[:-keep_recent]
    recent = msgs[-keep_recent:]
    count = int(compress_count or 0)
    summary = existing_summary
    if model is not None and should_use_llm_summary(older, compress_count=count):
        try:
            summary = await summarize_messages(older, existing_summary, model)
        except Exception:  # noqa: BLE001
            summary = existing_summary or "(压缩调用失败，保留最近消息)"
        count += 1
    summary = enforce_precision(summary, older)
    return CompressionResult(
        summary=summary,
        recent=recent,
        triggered=True,
        tokens=total,
        keep_tokens=0,
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
    "summarize_messages",
    "tail_snapshot",
    "tail_start_id",
]
