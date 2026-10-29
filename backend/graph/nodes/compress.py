"""上下文压缩节点（对齐 agent.md §4.3）。

核心原则：宁可多留废话，不可丢精确值。
M1 提供「保留最近 + 摘要更早」的骨架；完整 9 段式压缩见 M2 的 `cognition/compressor.py`。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import SystemMessage

from harness.compression import compress_graph_messages, tail_start_id

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 版本差异兜底
    from langchain_core.messages import RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
except Exception:  # noqa: BLE001
    RemoveMessage = None  # type: ignore
    REMOVE_ALL_MESSAGES = None  # type: ignore

KEEP_RECENT = 6


def make_compress_node(
    model: Any | None = None,
    keep_recent: int | None = None,
    compressor: Any | None = None,
    model_factory: Callable[[], Any] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    async def compress_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        existing = state.get("context_summary", "")

        # 每次调用时解析，支持 /api/config/summarization 热更新（无需重建图）。
        from config import settings

        active_model = model
        if model_factory is not None:
            try:
                active_model = model_factory()
            except Exception as exc:  # noqa: BLE001
                logger.warning("compress model resolve failed, rule-only: %s", exc)
                active_model = None
        active_keep = (
            max(1, int(settings.summarization_keep_messages or KEEP_RECENT))
            if keep_recent is None
            else max(1, int(keep_recent))
        )

        if compressor is not None:
            # 用 cognition 的阈值 + 保留最近 token 的增量压缩（§4.3）
            # 阈值与目标模型绑定：目标窗口更小 → 更早压缩（agent-3 §2.2）。
            from cognition.compressor import COMPRESS_THRESHOLD, KEEP_RECENT_TOKENS

            threshold = int(state.get("compress_threshold") or COMPRESS_THRESHOLD)
            keep_tokens = int(state.get("keep_recent_tokens") or KEEP_RECENT_TOKENS)
            try:
                try:
                    result = await compressor(
                        messages, existing, active_model,
                        threshold=threshold, keep_tokens=keep_tokens,
                    )
                except TypeError:
                    # 兼容旧的三参数 compressor（测试/自定义注入）
                    result = await compressor(messages, existing, active_model)
            except Exception as e:  # noqa: BLE001
                logger.warning("compressor failed, skip: %s", e)
                return {"token_usage": {"compressed": False}, "context_compressed": True}
            if not result.get("triggered"):
                return {"token_usage": {"compressed": False}, "context_compressed": True}
            summary = result.get("summary") or existing
            recent = result.get("recent") or messages
            payload: dict[str, Any] = {
                "context_summary": summary,
                "token_usage": {"compressed": True},
                "context_compressed": True,
            }
            tail_id = tail_start_id(recent)
            if tail_id:
                payload["context_tail_start_id"] = tail_id
            if RemoveMessage is not None and REMOVE_ALL_MESSAGES is not None:
                payload["messages"] = [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    SystemMessage(content=f"[历史摘要]\n{summary}"),
                    *recent,
                ]
            return payload

        if len(messages) <= active_keep:
            return {"token_usage": {"compressed": True}, "context_compressed": True}

        result = await compress_graph_messages(
            messages,
            existing_summary=existing,
            model=active_model,
            compress_count=int(state.get("compress_count", 0) or 0),
            keep_recent=active_keep,
        )
        summary = result.summary
        payload: dict[str, Any] = {
            "context_summary": summary,
            "compress_count": result.compress_count,
            "token_usage": {"compressed": True},
            "context_compressed": True,
        }
        tail_id = result.tail_start_id
        if tail_id:
            payload["context_tail_start_id"] = tail_id
        if RemoveMessage is not None and REMOVE_ALL_MESSAGES is not None:
            payload["messages"] = [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                SystemMessage(content=f"[????]\n{summary}"),
                *result.recent,
            ]
        return payload

    return compress_node
