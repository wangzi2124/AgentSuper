"""上下文压缩节点（对齐 opencode compaction）。

- 触发由 `route_after_supervisor` 按可用窗口判断；
- tail 按最近 user 轮次 + token 预算保留（keep.tokens，默认 15k）；
- 摘要始终优先走模型，失败时用确定性兜底；
- 压缩检查点写入 state，并由 PlatformContext 同步到 session.db；
- 不物理删除历史，模型视角由 session.db 重建时过滤。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from harness.compression import (
    _min_gain_default,
    _preserve_default,
    compress_graph_messages,
    estimate_messages,
    tail_start_id,
)

logger = logging.getLogger(__name__)

KEEP_RECENT = 6


def _model_label(model: Any | None) -> str:
    value = getattr(model, "model_name", None)
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001
            value = None
    return str(value or getattr(model, "tier", "") or "")


def make_compress_node(
    model: Any | None = None,
    keep_recent: int | None = None,
    compressor: Any | None = None,
    model_factory: Callable[[], Any] | None = None,
    session_model_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    async def compress_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        existing = state.get("context_summary", "")

        from config import settings

        active_model = model
        if model_factory is not None:
            try:
                active_model = model_factory()
            except Exception as exc:  # noqa: BLE001
                logger.warning("compress model resolve failed, rule-only: %s", exc)
                active_model = None
        # 未显式配置压缩模型时，按 opencode 行为回退到本回合主模型。
        if active_model is not None and not getattr(settings, "compress_model", None):
            if session_model_factory is not None:
                try:
                    active_model = session_model_factory(state)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("session model resolve failed, keep compress model: %s", exc)

        active_keep = (
            max(0, int(settings.compaction_keep_tokens or 0))
            if keep_recent is None
            else max(1, int(keep_recent))
        )
        tokens_before = estimate_messages(messages)

        if compressor is not None:
            threshold = int(state.get("compress_threshold") or 0)
            keep_tokens = int(state.get("keep_recent_tokens") or 0)
            try:
                result = await compressor(
                    messages, existing, active_model,
                    threshold=threshold, keep_tokens=keep_tokens,
                )
            except TypeError:
                try:
                    result = await compressor(messages, existing, active_model)
                except Exception as e:  # noqa: BLE001
                    logger.warning("compressor failed, skip: %s", e)
                    return {"token_usage": {"compressed": False}, "context_compressed": True}
            except Exception as e:  # noqa: BLE001
                logger.warning("compressor failed, skip: %s", e)
                return {"token_usage": {"compressed": False}, "context_compressed": True}
            if not result.get("triggered"):
                return {"token_usage": {"compressed": False}, "context_compressed": True}
            summary = result.get("summary") or existing
            recent = result.get("recent") or messages
            tail_id = tail_start_id(recent)
            return _compaction_payload(
                summary=summary,
                recent=recent,
                tail_id=tail_id,
                reason="auto",
                tokens_before=tokens_before,
                model_label=_model_label(active_model),
            )

        if len(messages) <= active_keep:
            return {"token_usage": {"compressed": True}, "context_compressed": True}

        legacy_keep = keep_recent is not None
        threshold = int(state.get("compress_threshold") or 0)
        keep_tokens = (
            0 if legacy_keep else int(state.get("keep_recent_tokens") or 0)
        )
        if not legacy_keep and keep_tokens <= 0:
            keep_tokens = _preserve_default(threshold)
        result = await compress_graph_messages(
            messages,
            existing_summary=existing,
            model=active_model,
            compress_count=int(state.get("compress_count", 0) or 0),
            keep_recent=active_keep,
            keep_tokens=keep_tokens,
            tail_turns=None if legacy_keep else None,
            min_gain_tokens=0 if legacy_keep else _min_gain_default(threshold),
        )
        if not getattr(result, "triggered", False):
            return {"token_usage": {"compressed": False}, "context_compressed": True}
        return _compaction_payload(
            summary=result.summary,
            recent=result.recent,
            tail_id=result.tail_start_id,
            reason="auto",
            tokens_before=tokens_before,
            tokens_after=estimate_messages(result.recent) + len(result.summary or ""),
            model_label=_model_label(active_model),
            compress_count=result.compress_count,
        )

    return compress_node


def _compaction_payload(
    *,
    summary: str,
    recent: list[Any],
    tail_id: str | None,
    reason: str,
    tokens_before: int,
    tokens_after: int | None = None,
    model_label: str = "",
    compress_count: int | None = None,
) -> dict[str, Any]:
    after = int(tokens_after if tokens_after is not None else estimate_messages(recent))
    payload: dict[str, Any] = {
        "context_summary": summary,
        "token_usage": {"compressed": True},
        "context_compressed": True,
        "compaction_event": {
            "summary": summary,
            "reason": reason,
            "tail_start_id": tail_id,
            "tokens_before": int(tokens_before),
            "tokens_after": after,
            "model": model_label,
        },
    }
    if tail_id:
        payload["context_tail_start_id"] = tail_id
    if compress_count is not None:
        payload["compress_count"] = compress_count
    return payload
