"""权限管理 API 路由模块。

提供待处理权限请求的查询和响应功能。
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.permission import get_manager
from app.api.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


class RespondRequest(BaseModel):
    """权限响应请求模型。"""
    decision: str  # "allowed" | "denied"
    remember: bool = False


class PendingResponse(BaseModel):
    """待处理权限请求响应模型。"""
    id: str
    path: str
    operation: str
    tool_name: str
    tool_args: dict
    created_at: str


@router.get("/permission/pending", tags=["Permission"])
async def list_pending():
    """获取所有待处理的权限请求列表。"""
    mgr = get_manager()
    requests = mgr.get_pending_requests()
    return {
        "pending": [
            {
                "id": r.id,
                "path": r.path,
                "operation": r.operation,
                "tool_name": r.tool_name,
                "tool_args": r.tool_args,
                "created_at": r.created_at.isoformat(),
            }
            for r in requests
        ]
    }


@router.post("/permission/request/{request_id}/respond", tags=["Permission"])
async def respond(request_id: str, body: RespondRequest, request: Request):
    """响应指定的权限请求，允许或拒绝操作。"""
    require_admin(request)
    mgr = get_manager()
    ok = mgr.respond(request_id, body.decision, remember=body.remember)
    if not ok:
        raise HTTPException(status_code=404, detail="Request not found or already responded")
    return {"status": "ok"}
