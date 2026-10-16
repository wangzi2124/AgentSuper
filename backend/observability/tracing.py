"""Trace 接入（对齐 agent.md §11.3）。

优先使用 LangFuse（若已安装且配置了密钥）；否则降级为进程内 trace 记录，
绝不让可观测性阻断主流程。
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    name: str
    start: float
    end: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        end = self.end or time.time()
        return (end - self.start) * 1000.0


_SPANS: list[TraceSpan] = []
_MAX_SPANS = 2000


def _langfuse_available() -> bool:
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return False
    try:
        import langfuse  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def get_trace_handler(user_id: str, session_id: str) -> Any | None:
    """返回 LangFuse CallbackHandler；不可用时返回 None。"""
    if not _langfuse_available():
        return None
    try:
        from langfuse.callback import CallbackHandler

        return CallbackHandler(
            user_id=user_id,
            session_id=session_id,
            tags=["agent-platform", "v1.0"],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("LangFuse handler init failed: %s", e)
        return None


@contextmanager
def trace_span(name: str, **metadata: Any) -> Iterator[TraceSpan]:
    """节点/工具级计时埋点，始终可用（不依赖 LangFuse）。"""
    span = TraceSpan(name=name, start=time.time(), metadata=dict(metadata))
    try:
        yield span
    finally:
        span.end = time.time()
        _SPANS.append(span)
        if len(_SPANS) > _MAX_SPANS:
            del _SPANS[: len(_SPANS) - _MAX_SPANS]


def recent_spans(limit: int = 100) -> list[TraceSpan]:
    return _SPANS[-limit:]
