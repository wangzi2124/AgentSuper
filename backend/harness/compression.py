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

from cognition.context_budget import (
    MIN_PRESERVE_TOKENS,
    ContextBudget,
    min_gain_budget,
    normalized_output,
    preserve_recent_budget,
)

logger = logging.getLogger(__name__)

DEFAULT_KEEP_TOKENS = 15_000
DEFAULT_BUFFER_RATIO = 0.15
DEFAULT_BUFFER_MIN = 16_000
# 对齐 opencode compaction.ts:DEFAULT_TAIL_TURNS = 2。
# <=0 表示「不保留 tail」（整段历史进入摘要），不再表示「不限轮数」。
DEFAULT_TAIL_TURNS = 2


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
        return int(tail_turns)
    try:
        from config import settings

        return int(getattr(settings, "compaction_tail_turns", DEFAULT_TAIL_TURNS) or 0)
    except Exception:  # noqa: BLE001
        return DEFAULT_TAIL_TURNS


def _keep_default(keep_tokens: int | None = None) -> int:
    """Explicit preserve budget (0 = caller should use the window formula)."""
    if keep_tokens is not None and keep_tokens > 0:
        return int(keep_tokens)
    try:
        from config import settings

        return max(0, int(getattr(settings, "compaction_keep_tokens", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0


def _normalized_output(window: int, max_output_tokens: int) -> int:
    """Compatibility wrapper around ``cognition.context_budget``."""
    return normalized_output(window, max_output_tokens)


def _explicit_reserved() -> int:
    """``compaction_buffer`` absolute token override (percent is legacy-only)."""
    try:
        from config import settings

        raw = str(getattr(settings, "compaction_buffer", "15%") or "").strip()
    except Exception:  # noqa: BLE001
        return 0
    if not raw or raw.endswith("%"):
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def _preserve_default(usable: int) -> int:
    """Compatibility wrapper around ``cognition.context_budget``."""
    return preserve_recent_budget(usable)


def _min_gain_default(usable: int) -> int:
    """Minimum saved tokens required to write a checkpoint."""
    try:
        from config import settings

        configured = int(getattr(settings, "compaction_min_gain_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        configured = 0
    if configured > 0:
        return configured
    return min_gain_budget(usable)


def _segment_budget(model: Any | None) -> int:
    """Chunk size for the summary request, sized to the compress model.

    The request may use most of the compress model's usable window; only the
    prompt template + previous summary need headroom.  Chunking too small
    turns one summary into many calls, which is the opposite of the goal.
    """
    try:
        from models.catalog import effective_context_length, read_limits

        name = getattr(model, "model_name", None)
        model_id = str(name() if callable(name) else (name or ""))
        window = int(effective_context_length(model_id) or 0)
        if window <= 0:
            return 8_000
        output = int(read_limits(model_id).get("max_output_tokens") or 0)
        usable = ContextBudget.plan(window, max_output_tokens=output).usable
        return max(4_000, int(usable * 0.8))
    except Exception:  # noqa: BLE001
        return 8_000


@dataclass(frozen=True)
class CompressionPlan:
    threshold: int
    keep_tokens: int
    tail_turns: int = DEFAULT_TAIL_TURNS
    min_gain: int = 0

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
        if window <= 0:
            usable = int(default_threshold)
            keep = (
                int(preserve_recent_tokens or 0)
                or _keep_default(None)
                or int(default_keep)
            )
            return cls(
                usable,
                max(MIN_PRESERVE_TOKENS, min(int(keep), usable)),
                turns,
                _min_gain_default(usable),
            )
        output = _normalized_output(int(window), max_output_tokens)
        reserved_tokens = int(reserved or 0) or _explicit_reserved()
        budget = ContextBudget.plan(
            int(window),
            max_output_tokens=output,
            reserved=reserved_tokens,
        )
        usable = budget.usable
        keep = (
            int(preserve_recent_tokens or 0)
            or _keep_default(None)
            or budget.preserve_recent
        )
        keep = max(MIN_PRESERVE_TOKENS, min(int(keep), usable))
        return cls(
            usable,
            keep,
            turns,
            _min_gain_default(usable),
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
    """Single token ruler: delegate to ContextMeter."""
    from cognition.context_meter import get_meter

    return get_meter().estimate(messages).messages


def _is_summary_message(msg: Any) -> bool:
    extra = getattr(msg, "additional_kwargs", None)
    if isinstance(extra, dict) and extra.get("compaction"):
        return True
    content = getattr(msg, "content", None)
    if isinstance(msg, dict):
        content = msg.get("content")
        extra = msg.get("additional_kwargs")
        if isinstance(extra, dict) and extra.get("compaction"):
            return True
    text = content if isinstance(content, str) else ""
    return text.startswith("[历史摘要]")


def _message_id(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("_id") or msg.get("id")
    return getattr(msg, "id", None)


def _message_seq(msg: Any) -> Any:
    if isinstance(msg, dict):
        return msg.get("_seq") or msg.get("seq")
    return getattr(msg, "seq", None)


def tail_start_id(recent: Sequence[Any]) -> str | None:
    if not recent:
        return None
    value = _message_id(recent[0])
    if value:
        return str(value)
    return None


def tail_snapshot(recent: Sequence[Any]) -> dict[str, Any]:
    """Return the epoch snapshot fields for the preserved recent tail."""
    if not recent:
        return {}
    first = recent[0]
    ident = _message_id(first)
    seq = _message_seq(first)
    out: dict[str, Any] = {}
    if ident:
        out["tail_start_id"] = str(ident)
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
    if tail_turns <= 0:
        # opencode: limit <= 0 means "keep no tail" (head = all messages).
        return msgs, []
    turns = turn_ranges(msgs)
    if not turns:
        return msgs, []
    considered = turns[-tail_turns:]

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
    from cognition.compressor import serialize_messages
    from cognition.token_counter import estimate_tokens
    from .compression_agent import CompressionAgent

    msgs = [m for m in messages if not _is_summary_message(m)]
    if not msgs:
        return existing_summary
    if model is None:
        from models.router import get_model

        model = get_model("simple")

    prompt_messages = [
        m for m in (list(full_messages) if full_messages else msgs)
        if not _is_summary_message(m)
    ]
    if segment_tokens <= 0:
        segment_tokens = _segment_budget(model)
    chunks: list[list[Any]] = [prompt_messages]
    if estimate_tokens(serialize_messages(prompt_messages)) > segment_tokens:
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

    agent = CompressionAgent(model)
    summary = existing_summary
    try:
        for chunk in chunks:
            text = await agent.summarize(
                summary, serialize_messages(chunk, strip_media=True)
            )
            summary = text or summary
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
    threshold: int | None = 80_000,
    keep_tokens: int = DEFAULT_KEEP_TOKENS,
    tail_turns: int | None = None,
    keep_recent: int | None = None,
    compress_count: int = 0,
    segment_tokens: int = 0,
    require_precision: bool = False,
    raise_on_error: bool = False,
    require_llm: bool = False,
    min_gain_tokens: int = 0,
) -> CompressionResult:
    """Compress a history through the single canonical seam.

    ``threshold=None`` disables the pre-gate (callers that already decided to
    compress, e.g. the graph auto node, pass ``0``); ``keep_recent`` selects the
    legacy message-count tail and is only used when ``keep_tokens <= 0``.
    """
    from cognition.compressor import (
        build_fallback_summary,
        enforce_precision,
        extract_precision_values,
        normalize_precision_text,
        serialize_messages,
    )

    msgs = [m for m in messages if not _is_summary_message(m)]
    total = estimate_messages(msgs)
    gate = int(threshold) if threshold is not None else None
    if gate is not None and total <= gate:
        return CompressionResult(
            summary=existing_summary,
            recent=msgs,
            triggered=False,
            tokens=total,
            threshold=gate,
            keep_tokens=keep_tokens,
            compress_count=compress_count,
        )

    if keep_recent is not None and keep_recent > 0 and keep_tokens <= 0:
        older, recent = msgs[:-keep_recent], msgs[-keep_recent:]
    else:
        older, recent = select_tail_messages(
            msgs, keep_tokens, _tail_turns_default(tail_turns)
        )
    older_tokens = estimate_messages(older)
    if not older or (min_gain_tokens > 0 and older_tokens < min_gain_tokens):
        return CompressionResult(
            summary=existing_summary,
            recent=msgs,
            triggered=False,
            tokens=total,
            threshold=int(gate or 0),
            keep_tokens=keep_tokens,
            compress_count=compress_count,
        )

    count = int(compress_count or 0)
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
        count += 1
    if not str(summary or "").strip():
        summary = build_fallback_summary(older, existing_summary)

    summary = enforce_precision(summary, older)
    preserved = normalize_precision_text(
        f"{summary}\n{serialize_messages(recent, strip_media=True)}"
    )
    facts = extract_precision_values(serialize_messages(msgs, strip_media=True))
    missing = [fact for fact in facts if fact not in preserved]
    if missing and require_precision:
        raise CompressionError("压缩结果缺失精确值：" + ", ".join(missing[:5]))

    return CompressionResult(
        summary=summary,
        recent=recent,
        triggered=True,
        tokens=total,
        threshold=int(gate or 0),
        keep_tokens=keep_tokens,
        tail_start_id=tail_start_id(recent),
        missing_precision=missing,
        compress_count=count,
    )


@dataclass(frozen=True)
class CompactionPolicy:
    """Adapter-specific knobs; everything else comes from ``CompressionPlan``."""

    reason: str = "auto"
    threshold: int | None = None
    keep_tokens: int | None = None
    tail_turns: int | None = None
    min_gain_tokens: int | None = None
    segment_tokens: int = 0
    require_llm: bool = False
    require_precision: bool = False
    raise_on_error: bool = False
    keep_recent: int | None = None
    compress_count: int = 0

    @classmethod
    def for_auto(
        cls,
        *,
        usable: int,
        keep_recent: int,
        keep_tokens: int = 0,
        legacy: bool = False,
        compress_count: int = 0,
        segment_tokens: int = 0,
    ) -> "CompactionPolicy":
        """Build the graph auto policy.

        ``usable`` only sizes the tail and min-gain budgets; the router already
        decided to compress, so ``threshold=0`` disables a second gate.
        """
        resolved_keep = (
            0
            if legacy
            else (int(keep_tokens) if keep_tokens > 0 else _preserve_default(usable))
        )
        return cls(
            reason="auto",
            threshold=0,
            keep_tokens=resolved_keep,
            min_gain_tokens=0 if legacy else _min_gain_default(usable),
            keep_recent=keep_recent,
            compress_count=compress_count,
            segment_tokens=segment_tokens,
        )


class ContextCompactor:
    """Public compression seam for auto / manual / switch / overflow adapters.

    Every caller resolves a ``CompactionPolicy`` (or injects one) and goes
    through ``plan`` + ``compress``; there is no second compression entry.
    """

    def plan(
        self,
        *,
        context_window: int,
        max_output_tokens: int = 0,
        preserve_recent_tokens: int = 0,
        tail_turns: int | None = None,
    ) -> CompressionPlan:
        return CompressionPlan.for_window(
            context_window,
            max_output_tokens=max_output_tokens,
            preserve_recent_tokens=preserve_recent_tokens,
            tail_turns=tail_turns,
        )

    async def compress(
        self,
        messages: Sequence[Any],
        *,
        existing_summary: str = "",
        model: Any | None = None,
        policy: CompactionPolicy | None = None,
        plan: CompressionPlan | None = None,
    ) -> CompressionResult:
        policy = policy or CompactionPolicy()
        plan = plan or CompressionPlan.for_window(0)
        threshold = (
            policy.threshold
            if policy.threshold is not None
            else plan.threshold
        )
        keep_tokens = (
            policy.keep_tokens
            if policy.keep_tokens is not None
            else plan.keep_tokens
        )
        tail_turns = (
            policy.tail_turns
            if policy.tail_turns is not None
            else plan.tail_turns
        )
        min_gain = (
            policy.min_gain_tokens
            if policy.min_gain_tokens is not None
            else plan.min_gain
        )
        return await compress_messages(
            messages,
            existing_summary=existing_summary,
            model=model,
            threshold=threshold,
            keep_tokens=keep_tokens,
            tail_turns=tail_turns,
            keep_recent=policy.keep_recent,
            compress_count=policy.compress_count,
            segment_tokens=policy.segment_tokens,
            require_precision=policy.require_precision,
            raise_on_error=policy.raise_on_error,
            require_llm=policy.require_llm,
            min_gain_tokens=min_gain,
        )


__all__ = [
    "CompactionPolicy",
    "CompressionError",
    "CompressionPlan",
    "CompressionResult",
    "ContextCompactor",
    "compress_messages",
    "estimate_messages",
    "select_tail_messages",
    "summarize_messages",
    "tail_snapshot",
    "tail_start_id",
    "turn_ranges",
]
