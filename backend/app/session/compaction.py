"""Single owner of compression watermarks.

The compaction row in ``session_messages`` is authoritative; the context epoch
is a query cache.  All readers and writers (auto, manual, switch, overflow,
undo, startup repair) cross this module instead of re-deriving summary/tail
from graph state or from their own copy of the epoch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from . import repository
from .models import Message

logger = logging.getLogger(__name__)

_SUMMARY_PREFIX = "[历史摘要]"


@dataclass(frozen=True)
class Checkpoint:
    session_id: str
    seq: int
    summary: str
    tail_start_id: str | None
    tail_start_seq: int | None
    tokens_before: int
    tokens_after: int
    reason: str
    model: str

    @classmethod
    def from_message(cls, session_id: str, msg: Message) -> "Checkpoint":
        data = msg.data or {}
        tail = data.get("tail_start_id")
        return cls(
            session_id=session_id,
            seq=int(msg.seq or 0),
            summary=str(data.get("summary") or data.get("content") or ""),
            tail_start_id=str(tail) if tail else None,
            tail_start_seq=(
                int(data["tail_start_seq"])
                if isinstance(data.get("tail_start_seq"), int)
                else None
            ),
            tokens_before=int(data.get("tokens_before") or 0),
            tokens_after=int(data.get("tokens_after") or 0),
            reason=str(data.get("reason") or data.get("mode") or "auto"),
            model=str(data.get("model") or ""),
        )


@dataclass(frozen=True)
class ContextView:
    summary: str
    messages: list[Any]
    checkpoint_seq: int | None = None
    tail_start_id: str | None = None


def _is_summary_message(msg: Any) -> bool:
    extra = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(extra, dict) and extra.get("compaction"):
        content = getattr(msg, "content", "")
        return isinstance(content, str) and content.startswith(_SUMMARY_PREFIX)
    content = getattr(msg, "content", "")
    return isinstance(content, str) and content.startswith(_SUMMARY_PREFIX)


class CompactionCheckpoint:
    """Write/read/repair the one compression watermark for a session."""

    @staticmethod
    def latest(
        session_id: str,
        *,
        hidden_ids: Optional[set[str]] = None,
        baseline: Optional[int] = None,
    ) -> Optional[Checkpoint]:
        msg = repository.latest_compaction(session_id, hidden_ids=hidden_ids)
        if msg is None:
            return None
        if baseline is not None and int(baseline) > 0 and int(msg.seq or 0) > int(baseline):
            # Undo went back before this checkpoint: it is no longer valid.
            return None
        return Checkpoint.from_message(session_id, msg)

    @staticmethod
    def record(session_id: str, event: dict[str, Any]) -> Optional[Checkpoint]:
        """Persist a compaction event; idempotent for identical watermarks."""
        if not isinstance(event, dict):
            return None
        summary = str(event.get("summary") or "").strip()
        if not summary:
            return None
        reason = str(event.get("reason") or "auto")
        tail_start_id = event.get("tail_start_id")
        tail_start_id = str(tail_start_id) if tail_start_id else None
        tail_start_seq = event.get("tail_start_seq")
        if not isinstance(tail_start_seq, int) and tail_start_id:
            tail_start_seq = repository.seq_of_message(session_id, tail_start_id)
        data = {
            "content": summary,
            "mode": reason,
            "reason": reason,
            "tail_start_id": tail_start_id,
            "tail_start_seq": tail_start_seq,
            "tokens_before": int(event.get("tokens_before") or 0),
            "tokens_after": int(event.get("tokens_after") or 0),
            "model": str(event.get("model") or ""),
        }

        previous = repository.latest_compaction(session_id)
        if previous is not None:
            prev_data = previous.data or {}
            if (
                str(prev_data.get("summary") or prev_data.get("content") or "") == summary
                and (prev_data.get("tail_start_id") or None) == tail_start_id
            ):
                return Checkpoint.from_message(session_id, previous)

        epoch = repository.get_epoch(session_id)
        snapshot = dict(epoch.snapshot or {}) if epoch else {}
        snapshot.update(
            {
                "summary": summary,
                "tail_start_id": tail_start_id,
                "compaction_reason": reason,
                "compaction_tokens_before": data["tokens_before"],
                "compaction_tokens_after": data["tokens_after"],
            }
        )
        try:
            msg = repository.record_compaction(
                session_id,
                data,
                snapshot,
                summary or (epoch.baseline if epoch else ""),
            )
        except Exception:  # noqa: BLE001 - checkpoint failure must not block a turn
            logger.warning("record compaction failed for %s", session_id, exc_info=True)
            return None
        return Checkpoint.from_message(session_id, msg)

    @staticmethod
    def rebuild_view(
        session_id: str,
        *,
        hidden_ids: Optional[set[str]] = None,
        baseline: Optional[int] = None,
    ) -> ContextView:
        """Model view: ``[summary] + [tail messages]`` with old checkpoints hidden."""
        from .projector import rebuild_model_messages

        rendered = rebuild_model_messages(
            session_id, hidden_ids=hidden_ids, baseline=baseline
        )
        summary = ""
        tail: list[Any] = rendered
        if rendered and _is_summary_message(rendered[0]):
            content = str(getattr(rendered[0], "content", "") or "")
            summary = content[len(_SUMMARY_PREFIX):].lstrip("\n")
            tail = list(rendered[1:])
        checkpoint = CompactionCheckpoint.latest(
            session_id, hidden_ids=hidden_ids, baseline=baseline
        )
        return ContextView(
            summary=summary,
            messages=tail,
            checkpoint_seq=checkpoint.seq if checkpoint else None,
            tail_start_id=checkpoint.tail_start_id if checkpoint else None,
        )

    @staticmethod
    def repair(
        session_id: str, *, hidden_ids: Optional[set[str]] = None
    ) -> None:
        """Reconcile the epoch cache with the latest visible compaction row.

        Safe to call on every startup / first read; it is a no-op when the
        epoch already points at the same summary and tail.
        """
        msg = repository.latest_compaction(session_id, hidden_ids=hidden_ids)
        if msg is None:
            return
        checkpoint = Checkpoint.from_message(session_id, msg)
        epoch = repository.get_epoch(session_id)
        snapshot = dict(epoch.snapshot or {}) if epoch else {}
        if (
            str(snapshot.get("summary") or "") == checkpoint.summary
            and (snapshot.get("tail_start_id") or None) == checkpoint.tail_start_id
        ):
            return
        snapshot.update(
            {
                "summary": checkpoint.summary,
                "tail_start_id": checkpoint.tail_start_id,
                "compaction_reason": checkpoint.reason,
                "compaction_tokens_before": checkpoint.tokens_before,
                "compaction_tokens_after": checkpoint.tokens_after,
            }
        )
        try:
            repository.upsert_epoch(
                session_id,
                checkpoint.summary or (epoch.baseline if epoch else ""),
                int(msg.seq or 0),
                snapshot,
            )
            logger.info("repaired compaction epoch for %s at seq %s", session_id, msg.seq)
        except Exception:  # noqa: BLE001
            logger.warning("compaction epoch repair failed for %s", session_id, exc_info=True)


__all__ = ["Checkpoint", "CompactionCheckpoint", "ContextView"]
