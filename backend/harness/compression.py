"""Unified compression helpers for graph compaction, manual compact and switch.

Aligns with opencode v2 compaction:
- ``threshold = context_window - buffer`` where buffer defaults to
  ``max(15% × window, 16000)``;
- tail is chosen by user turns within ``keep.tokens`` (default 15k);
- summary requests reuse the full conversation and always use the model when
  one is available; deterministic fallback only on LLM failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger(__name__)

DEFAULT_KEEP_TOKENS = 15_000
DEFAULT_BUFFER_RATIO = 0.15
DEFAULT_BUFFER_MIN = 16_000
# 0 = 不限制轮数，仅按 keep_tokens 切分（opencode v2 行为）。
DEFAULT_TAIL_TURNS = 0


class CompressionError(RuntimeError):
    """Compression could not preserve required facts."""


def parse_buffer(value: Any, window: int) -> int:
    """Parse ``compaction_buffer`` (``"15%"`` or absolute tokens) into tokens."""
    try:
        raw = str(value or "").strip()
    except Exception:  # noqa: BLE001
        raw = ""
    if raw.endswith("%"):
        try:
            ratio = float(raw[:-1]) / 100.0
        except ValueError:
            ratio = DEFAULT_BUFFER_RATIO
        return max(DEFAULT_BUFFER_MIN, int(max(0.0, ratio) * max(0, window)))
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return max(DEFAULT_BUFFER_MIN, int(DEFAULT_BUFFER_RATIO * max(0, window)))


def _buffer_default(window: int) -> int:
    try:
        from config import settings

        configured = getattr(settings, "compaction_buffer", "15%")
    except Exception:  # noqa: BLE001
        configured = "15%"
    return parse_buffer(configured, int(window))


def _tail_turns_default(tail_turns: int | None = None) -> int:
    if tail_turns is not None:
        return max(0, int(tail_turns))
    return DEFAULT_TAIL_TURNS


def _keep_default(keep_tokens: int | None = None) -> int:
    if keep_tokens is not None and keep_tokens > 0:
        return int(keep_tokens)
    try:
        from config import settings

        configured = int(getattr(settings, "compaction_keep_tokens", DEFAULT_KEEP_TOKENS) or 0)
    except Exception:  # noqa: BLE001
        configured = 0
    return configured or DEFAULT_KEEP_TOKENS


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
        default_keep: int = DEFAULT_KEEP_TOKENS,
        reserved: int = 0,
        preserve_recent_tokens: int = 0,
        tail_turns: int | None = None,
        max_output_tokens: int = 0,
    ) -> "CompressionPlan":
        turns = _tail_turns_default(tail_turns)
        keep = _keep_default(preserve_recent_tokens or default_keep)
        if window <= 0:
            return cls(default_threshold, keep, turns)
        buffer = int(reserved or 0) or _buffer_default(int(window))
        usable = max(2_000, int(window) - buffer)
        return cls(usable, min(keep, usable), turns)


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
    if not turns:
        return msgs, []
    considered = turns if tail_turns <= 0 else turns[-tail_turns:]

    keep: int | None = None
    total = 0
    for start, end in reversed(considered):
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
    full_messages: Sequence[Any] | None = None,
) -> str:
    """Summarize a message slice; optional ``full_messages`` feeds the prompt.

    opencode v2 reuses the full conversation for the summary request while the
    prepared tail still comes from ``messages``. ``full_messages`` is only used
    to build the prompt text; the deterministic fallback keeps using ``messages``.
    """
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

    prompt_messages = list(full_messages) if full_messages else msgs
    chunks: list[list[Any]] = [prompt_messages]
    if (
        segment_tokens > 0
        and estimate_tokens(serialize_messages(prompt_messages)) > segment_tokens
    ):
        chunks = []
        current: list[Any] = []
        used = 0
        for msg in prompt_messages:
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
    keep_tokens: int = DEFAULT_KEEP_TOKENS,
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
    use_llm = require_llm or model is not None
    if use_llm:
        summary = await summarize_messages(
            older,
            existing_summary,
            model,
            segment_tokens=segment_tokens,
            raise_on_error=raise_on_error,
            full_messages=msgs,
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
    )

    msgs = list(messages)
    total = estimate_messages(msgs)
    if keep_tokens > 0 or tail_turns is not None or keep_recent <= 0:
        older, recent = select_tail_messages(
            msgs, _keep_default(keep_tokens), _tail_turns_default(tail_turns)
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
    use_llm = require_llm or model is not None
    if use_llm:
        summary = await summarize_messages(
            older, existing_summary, model, full_messages=msgs
        )
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
