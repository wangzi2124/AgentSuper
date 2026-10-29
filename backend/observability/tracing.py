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

from config import settings

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


def _sync_langfuse_env() -> None:
    """把 config.json 中的 Langfuse 配置补进进程环境（litellm/langfuse 直接读 env）。"""
    for env_key, value in (
        ("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key),
        ("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key),
        ("LANGFUSE_HOST", settings.langfuse_host),
    ):
        if value:
            os.environ.setdefault(env_key, value)


def configure_langfuse() -> bool:
    """按 config.json 的 langfuse_* 启用 litellm 回调（未安装 langfuse 时跳过）。"""
    if not _langfuse_available():
        return False
    try:
        import litellm

        for attr in ("success_callback", "failure_callback"):
            current = list(getattr(litellm, attr, None) or [])
            if "langfuse" not in current:
                current.append("langfuse")
            setattr(litellm, attr, current)
        logger.info("Langfuse callbacks enabled")
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse callback setup failed", exc_info=True)
        return False


def _langfuse_available() -> bool:
    _sync_langfuse_env()
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
