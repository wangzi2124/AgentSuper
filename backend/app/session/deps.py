"""FastAPI 隔离依赖。

对齐 opencode session-location.ts 中间件：任何 session 操作都先解析
User + Project + Session，校验归属后注入「会话上下文」。

隔离模型（三级）：
  User (X-User-Id)  →  Project (工作区根)  →  Session
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

from . import repository
from .models import ProjectInfo, SessionInfo
from .service import SessionService

_DEFAULT_USER = "anonymous"


def get_user_id(request: Request) -> str:
    """提取用户身份（对齐 chat.py 的 X-User-Id 约定）。"""
    uid = request.headers.get("X-User-Id", "")
    return uid.strip() if uid.strip() else _DEFAULT_USER


@dataclass
class SessionContext:
    """一次会话操作的作用域上下文。"""

    user_id: str
    session: SessionInfo
    project: ProjectInfo
    service: SessionService

    @property
    def session_id(self) -> str:
        return self.session.id


def discover_project_root(directory: str = "") -> str:
    """探测项目根：优先最近 git 仓库根，否则回退到默认工作区。

    对齐 opencode ProjectV2.resolve（git root discovery）。
    """
    d = Path(directory or ".").resolve()
    for candidate in [d, *d.parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return str(d)


def resolve_session_context(
    request: Request,
    session_id: str,
) -> SessionContext:
    """按 session 解析并注入隔离上下文；越权/不存在返回 403/404。

    对齐 opencode session-location.ts：从 session 行解析 directory，
    为该会话构建所属 project 的上下文。
    """
    service: SessionService = request.app.state.session_service
    user_id = get_user_id(request)
    session = repository.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id and session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    project = repository.get_project(session.project_id) or repository.resolve_project(
        discover_project_root(session.directory)
    )
    return SessionContext(user_id=user_id, session=session, project=project, service=service)


def create_project_context(
    request: Request,
    project_id: Optional[str] = None,
) -> tuple[str, str]:
    """列表/创建场景：解析 user + project（无 session 依赖）。"""
    user_id = get_user_id(request)
    if project_id:
        project = repository.get_project(project_id)
        root = project.root if project else discover_project_root()
    else:
        root = discover_project_root()
        project = repository.resolve_project(root)
        project_id = project.id
    return user_id, project_id
