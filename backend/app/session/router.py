"""/api/sessions REST 路由。

对应设计文档 §8.2。创建/列表场景用 create_project_context 解析 User+Project；
操作既有会话用 resolve_session_context 注入 SessionContext（隔离中间件）。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from . import repository
from .deps import SessionContext, create_project_context, get_user_id, resolve_session_context
from .models import PromptRequest, RevertRequest, SessionCreate, SessionInfo, SessionStatus, SessionUpdate

router = APIRouter()


def _service(request: Request):
    return request.app.state.session_service


@router.post("", response_model=SessionInfo)
async def create_session(
    body: SessionCreate,
    request: Request,
):
    """创建会话（支持父会话 → 子会话）。"""
    user_id, project_id = create_project_context(
        request, body.project_id
    ) if not body.parent_id else (get_user_id(request), None)
    service = _service(request)
    if body.parent_id:
        # 校验父会话归属
        resolve_session_context(request, body.parent_id)
        parent = repository.get_session(body.parent_id)
        project_id = parent.project_id
        directory = parent.directory
    else:
        directory = body.directory or ""
    session = service.create(
        user_id,
        project_id=project_id,
        directory=directory,
        parent_id=body.parent_id,
        agent=body.agent,
        model=body.model.model_dump() if body.model else None,
        kind=body.kind,
        title=body.title,
    )
    return session


@router.get("", response_model=list[SessionInfo])
async def list_sessions(
    request: Request,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    roots: bool = False,
    search: Optional[str] = None,
    archived: bool = False,
    limit: int = 100,
):
    user_id = get_user_id(request)
    return _service(request).list_sessions(
        user_id,
        project_id=project_id,
        workspace_id=workspace_id,
        roots_only=roots,
        search=search,
        archived=archived,
        limit=limit,
    )


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(request: Request, ctx: SessionContext = Depends(resolve_session_context)):
    return ctx.session


@router.patch("/{session_id}", response_model=SessionInfo)
async def update_session(body: SessionUpdate, ctx: SessionContext = Depends(resolve_session_context)):
    return ctx.service.update(
        ctx.user_id, ctx.session_id,
        title=body.title, agent=body.agent,
        model=body.model.model_dump() if body.model else None,
        archived=body.archived,
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(ctx: SessionContext = Depends(resolve_session_context)):
    ctx.service.remove(ctx.user_id, ctx.session_id)


@router.post("/{session_id}/fork", response_model=SessionInfo)
async def fork_session(
    request: Request,
    message_id: Optional[str] = None,
    ctx: SessionContext = Depends(resolve_session_context),
):
    return await ctx.service.fork(ctx.user_id, ctx.session_id, message_id=message_id)


@router.post("/{session_id}/prompt", response_model=dict)
async def prompt_session(body: PromptRequest, ctx: SessionContext = Depends(resolve_session_context)):
    """投递输入到会话并唤醒执行（delivery: steer|queue）。"""
    input_id = ctx.service.prompt(
        ctx.user_id, ctx.session_id, body.prompt, body.files, delivery=body.delivery
    )
    return {"input_id": input_id, "session_id": ctx.session_id}


@router.get("/{session_id}/messages", response_model=list)
async def list_messages(
    request: Request,
    after_seq: int = 0,
    limit: Optional[int] = None,
    ctx: SessionContext = Depends(resolve_session_context),
):
    return [m.model_dump() for m in ctx.service.messages(ctx.user_id, ctx.session_id, after_seq, limit)]


@router.get("/{session_id}/context", response_model=dict)
async def session_context(ctx: SessionContext = Depends(resolve_session_context)):
    """模型视角上下文（epoch + 过滤后的历史）。"""
    return ctx.service.context(ctx.user_id, ctx.session_id)


@router.post("/{session_id}/compact", status_code=204)
async def compact_session(
    request: Request,
    checkpoint: Optional[str] = None,
    ctx: SessionContext = Depends(resolve_session_context),
):
    await ctx.service.compact(ctx.user_id, ctx.session_id, checkpoint or "")


@router.post("/{session_id}/revert", response_model=dict)
async def revert_session(
    body: RevertRequest,
    ctx: SessionContext = Depends(resolve_session_context),
):
    """撤销到指定消息（删除其后的消息与部件）。"""
    return await ctx.service.revert(ctx.user_id, ctx.session_id, body.message_id)


@router.post("/{session_id}/interrupt", status_code=204)
async def interrupt_session(ctx: SessionContext = Depends(resolve_session_context)):
    await ctx.service.interrupt(ctx.user_id, ctx.session_id)


@router.get("/{session_id}/children", response_model=list[SessionInfo])
async def children(ctx: SessionContext = Depends(resolve_session_context)):
    return ctx.service.children(ctx.user_id, ctx.session_id)


@router.get("/{session_id}/status", response_model=SessionStatus)
async def status(ctx: SessionContext = Depends(resolve_session_context)):
    return ctx.service.status(ctx.user_id, ctx.session_id)
