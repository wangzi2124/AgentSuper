"""审批队列（内存实现，已接入高危工具审批链路）。

用于 `require_approval=True` 的工具审批：`PlatformContext` 请求审批，
前端/管理端通过 `/api/admin/approvals` 查询与决策。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

APPROVAL_TIMEOUT_SEC = 300


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    args: dict[str, Any]
    requester: str = "agent"
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending/approved/denied/expired
    decided_at: float | None = None
    # 等待决策的一次性事件（decide/expire 时置位）——供 await_decision 挂起
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)


class ApprovalQueue:
    """内存审批队列（生产可替换为 Redis/DB）。"""

    def __init__(self) -> None:
        self._queue: dict[str, ApprovalRequest] = {}
        self._lock = asyncio.Lock()

    async def request(self, tool: str, args: dict[str, Any], **kwargs: Any) -> str:
        req = ApprovalRequest(
            id=f"apr_{int(time.time()*1000)}_{hash((tool,str(args))) & 0xFFFF}",
            tool=tool,
            args=args,
            **kwargs,
        )
        async with self._lock:
            self._queue[req.id] = req
        return req.id

    async def decide(self, req_id: str, approved: bool, reason: str = "") -> bool:
        async with self._lock:
            req = self._queue.get(req_id)
            if not req:
                return False
            req.status = "approved" if approved else "denied"
            req.decided_at = time.time()
            req.reason = reason
        req._event.set()
        return True

    async def await_decision(self, req_id: str, timeout: float = APPROVAL_TIMEOUT_SEC) -> str:
        """挂起等待审批决策；超时置 expired。返回 status（approved/denied/expired）。"""
        req = self._queue.get(req_id)
        if not req:
            return "expired"
        try:
            await asyncio.wait_for(req._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            async with self._lock:
                if req.status == "pending":
                    req.status = "expired"
                    req.decided_at = time.time()
            return "expired"
        return req.status

    async def get(self, req_id: str) -> ApprovalRequest | None:
        async with self._lock:
            req = self._queue.get(req_id)
            if not req:
                return None
            if req.status == "pending" and time.time() - req.created_at > APPROVAL_TIMEOUT_SEC:
                req.status = "expired"
                req.decided_at = time.time()
                req._event.set()
            return req

    async def list(self, status: str | None = None) -> list[ApprovalRequest]:
        """列出审批请求（可按 status 过滤），按创建时间倒序。"""
        async with self._lock:
            items = list(self._queue.values())
        if status is not None:
            items = [r for r in items if r.status == status]
        return sorted(items, key=lambda r: r.created_at, reverse=True)

    async def cleanup(self) -> int:
        now = time.time()
        async with self._lock:
            expired = [rid for rid, r in self._queue.items() if r.status in ("approved","denied","expired") and (now - (r.decided_at or r.created_at)) > 3600]
            for rid in expired:
                del self._queue[rid]
            return len(expired)
