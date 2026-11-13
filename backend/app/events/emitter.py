"""v2 事件构造与发布（Phase 3）。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError

from app.contracts.v2 import (
    DURABLE_EVENT_TYPES,
    EventEnvelope,
    VOLATILE_EVENT_TYPES,
)
from app.session import repository as repo

logger = logging.getLogger(__name__)


def _session_location(ctx: Any, session_id: str) -> Any:
    """优先用内存会话补 location，避免每条事件（含每个文本增量）查一次库。"""
    sessions = getattr(ctx, "sessions", None)
    getter = getattr(sessions, "get", None)
    if callable(getter):
        try:
            info = getter(session_id)
        except Exception:  # noqa: BLE001 - 事件不能因会话缓存缺失而失败
            info = None
        if info is not None:
            return info
    try:
        return repo.get_session(session_id)
    except Exception:  # noqa: BLE001
        return None


async def emit_event(
    ctx: Any,
    event_type: str,
    data: dict[str, Any],
    *,
    session_id: str | None = None,
    aggregate_id: str | None = None,
) -> dict[str, Any] | None:
    bus = getattr(ctx, "events", None)
    if bus is None:
        return None
    created = int(time.time() * 1000)
    event: dict[str, Any] = {
        "id": f"evt_{uuid.uuid4().hex[:16]}",
        "created": created,
        "type": event_type,
        "data": data,
        "durable": None,
    }
    if session_id:
        info = _session_location(ctx, session_id)
        if info is not None:
            event["location"] = {
                "directory": info.directory,
                "project_id": getattr(info, "project_id", None),
            }
    if event_type in DURABLE_EVENT_TYPES:
        agg = aggregate_id or session_id or "global"
        # durable outbox 写库放线程，避免 BEGIN IMMEDIATE 竞争时卡住事件循环。
        seq = await asyncio.to_thread(
            repo.append_event, event["id"], agg, event_type, data, created
        )
        event["durable"] = {"aggregate_id": agg, "seq": seq, "version": 2}
    elif event_type in VOLATILE_EVENT_TYPES:
        event["durable"] = None
    else:
        logger.warning("unknown v2 event type: %s", event_type)
        return None
    try:
        EventEnvelope.model_validate(event)
    except ValidationError:
        logger.warning("invalid v2 event payload: %s", event_type, exc_info=True)
        return None
    await bus.publish(event)
    return event
