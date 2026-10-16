"""全链路日志查询与管理 API（`/api/logs`）。

供前端「日志管理」页消费：列表（trace 聚合 / 明细条目）、单条链路时间线、
聚合统计、筛选下拉取值、以及清理/删除。

鉴权策略：
- 读接口（列表/详情/统计/取值）走登录鉴权（AuthMiddleware 已在 /api/* 统一拦截），
  但仍按调用者 user_id 做数据隔离，避免看到别人的链路。
- 破坏性操作（clear / cleanup / delete trace）额外要求管理员。
"""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request

from app.api.deps import require_admin
from app.api.responses import ok

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _caller(request: Request) -> str:
    """取当前调用者 user_id（AuthMiddleware 注入），未登录时为空。"""
    return str(getattr(request.state, "user_id", "") or request.headers.get("X-User-Id", "") or "")


def _store():
    from app.chainlog.tracer import store

    return store()


def _scope(request: Request) -> str:
    """数据隔离范围：管理员看全部（空串），普通用户只看自己的 user_id。

    隔离条件下推到 store 的 SQL 里（`user_id = ? OR user_id = ''`），
    这样分页 total 与聚合统计都按自己的数据算，不会泄漏别人的活动量。
    """
    if _is_admin(request):
        return ""
    return _caller(request)


def _bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


@router.get("")
def list_logs(
    request: Request,
    view: str = Query("traces", pattern="^(traces|entries)$"),
    trace_id: str = "",
    session_id: str = "",
    level: str = "",
    stage: str = "",
    component: str = "",
    agent_id: str = "",
    keyword: str = "",
    has_error: bool = False,
    since: Optional[int] = None,
    until: Optional[int] = None,
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=500),
):
    """日志查询。

    - `view=traces`（默认）：按 trace 聚合，一次请求一行摘要，用于日志列表页。
    - `view=entries`：原始条目分页，用于「原始日志」表。
    """
    st = _store()
    scope = _scope(request)
    if view == "entries":
        return ok(st.list_entries(
            trace_id=trace_id, session_id=session_id, level=level, stage=stage,
            component=component, agent_id=agent_id, keyword=keyword,
            since=since, until=until, order=order, offset=offset, limit=limit,
            user_id=scope,
        ))
    return ok(st.list_traces(
        session_id=session_id, level=level, stage=stage, component=component,
        agent_id=agent_id, keyword=keyword, has_error=has_error,
        since=since, until=until, offset=offset, limit=limit, user_id=scope,
    ))


@router.get("/traces/{trace_id}")
def trace_detail(
    request: Request,
    trace_id: str,
    limit: int = Query(2000, ge=1, le=20000),
):
    """单条链路的时间线：按 seq 升序返回全部节点，前端据此还原「一次请求发生了什么」。"""
    st = _store()
    return ok(st.trace_detail(trace_id, limit=limit, user_id=_scope(request)))


@router.get("/stats")
def logs_stats(
    request: Request,
    session_id: str = "",
    since: Optional[int] = None,
    until: Optional[int] = None,
):
    """聚合统计：级别/阶段/组件分布、平均耗时、错误 Top、慢链路 Top。"""
    return ok(_store().stats(
        since=since, until=until, session_id=session_id, user_id=_scope(request),
    ))


@router.get("/filters")
def log_filters(request: Request):
    """筛选下拉取值：组件/阶段/级别/最近会话/最近 Agent。"""
    return ok(_store().distinct_values(user_id=_scope(request)))


@router.post("/cleanup")
def logs_cleanup(_: None = Depends(require_admin)):
    """按 TTL 与行数上限裁剪历史日志（手动触发；启动/定时任务也会自动执行）。"""
    return ok(_store().cleanup())


@router.post("/clear")
def logs_clear(
    _: None = Depends(require_admin),
    payload: dict = Body(default_factory=dict),
):
    """清理日志。可按 trace_id、单条时间水位或全量清空。"""
    st = _store()
    trace_id = str(payload.get("trace_id") or "")
    before = payload.get("before")
    return ok(st.clear(
        trace_id=trace_id,
        before=int(before) if before is not None else None,
        all_rows=bool(payload.get("all")) or _bool(str(payload.get("all_rows") or "")),
    ))


def _is_admin(request: Request) -> bool:
    """判断当前请求是否具备管理员权限（复用 require_admin 的判定逻辑，不抛异常）。"""
    from app.config import settings

    token = settings.admin_token
    if token:
        auth = request.headers.get("Authorization", "")
        return auth == f"Bearer {token}"
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")
