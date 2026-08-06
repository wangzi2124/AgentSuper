"""权限管理 API 路由模块。

提供待处理权限请求的查询和响应功能，以及可写工作区的运行时管理。
"""

import logging
import os
import string
from pathlib import Path

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


class WorkspaceRequest(BaseModel):
    """新增工作区请求模型。"""
    path: str


def _rebuild_agent_prompt(request: Request):
    """工作区变更后热更新 Agent 系统提示词（让 LLM 感知最新可写路径）。"""
    try:
        agent = getattr(request.app.state, "agent", None)
        if agent is not None and hasattr(agent, "rebuild_system_prompt"):
            agent.rebuild_system_prompt()
            logger.info("Agent system prompt rebuilt after workspace change")
    except Exception as e:
        logger.warning("Failed to rebuild agent system prompt: %s", e)


class PendingResponse(BaseModel):
    """待处理权限请求响应模型。"""
    id: str
    path: str
    operation: str
    tool_name: str
    tool_args: dict
    created_at: str


@router.get("/permission/pending", tags=["Permission"])
async def list_pending(request: Request):
    """获取所有待处理的权限请求列表。"""
    require_admin(request)
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


@router.get("/permission/browse", tags=["Permission"])
async def browse_dirs(request: Request, path: str = ""):
    """列出指定目录下的子目录（供前端工作目录选择器使用）。

    - path 为空：Windows 上返回所有盘符（C:\\, D:\\ 等），其他系统返回根目录 /
    - path 为目录：返回其子目录列表 + 父目录，供逐层下钻
    """
    require_admin(request)
    raw = (path or "").strip().strip('"').strip("'")

    if not raw:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            return {
                "path": "",
                "parent": "",
                "name": "",
                "dirs": [{"name": d.rstrip("\\"), "path": d} for d in drives],
            }
        raw = "/"

    p = Path(raw)
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {raw}")

    dirs: list[dict] = []
    try:
        for child in p.iterdir():
            try:
                if child.is_dir():
                    dirs.append({"name": child.name or str(child), "path": str(child)})
            except (PermissionError, OSError):
                continue
    except PermissionError:
        pass

    dirs.sort(key=lambda d: d["name"].lower())
    return {
        "path": str(p),
        "parent": str(p.parent) if str(p.parent) != str(p) else "",
        "name": p.name or str(p),
        "dirs": dirs,
    }


@router.get("/permission/workspaces", tags=["Permission"])
async def list_workspaces():
    """获取当前所有可写工作区（主工作区 + 额外工作区）。"""
    mgr = get_manager()
    return {"workspaces": mgr.list_workspaces()}


@router.post("/permission/workspaces", tags=["Permission"])
async def add_workspace(body: WorkspaceRequest, request: Request):
    """运行时新增可写工作区（免重启生效），并热更新 Agent 提示词。"""
    require_admin(request)
    raw = (body.path or "").strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")
    if not Path(raw).is_absolute():
        raise HTTPException(status_code=400, detail="path must be an absolute path, e.g. F:\\tetris")
    mgr = get_manager()
    resolved = mgr.add_workspace(raw)
    _rebuild_agent_prompt(request)
    return {"status": "ok", "path": str(resolved), "workspaces": mgr.list_workspaces()}


@router.delete("/permission/workspaces", tags=["Permission"])
async def remove_workspace(request: Request, path: str):
    """运行时移除可写工作区，并热更新 Agent 提示词。"""
    require_admin(request)
    mgr = get_manager()
    ok = mgr.remove_workspace(path)
    if not ok:
        raise HTTPException(status_code=404, detail="Workspace not found")
    _rebuild_agent_prompt(request)
    return {"status": "ok", "workspaces": mgr.list_workspaces()}
