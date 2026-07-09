import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.permission import get_manager

logger = logging.getLogger(__name__)
router = APIRouter()


class RespondRequest(BaseModel):
    decision: str  # "allowed" | "denied"
    remember: bool = False


class PendingResponse(BaseModel):
    id: str
    path: str
    operation: str
    tool_name: str
    tool_args: dict
    created_at: str


@router.get("/permission/pending", tags=["Permission"])
async def list_pending():
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
async def respond(request_id: str, body: RespondRequest):
    mgr = get_manager()
    ok = mgr.respond(request_id, body.decision, remember=body.remember)
    if not ok:
        raise HTTPException(status_code=404, detail="Request not found or already responded")
    return {"status": "ok"}
