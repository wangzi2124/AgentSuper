"""全链路日志（chain logging）。

把「一次请求从进入系统到落库返回」之间发生的每个节点记进数据库，并在前端
「日志管理」页按 trace 时间线展示。

模块划分：
- `db`        chain_logs 子系统的连接与建表（sqlite 独立文件 / 非 sqlite 统一库）
- `store`     内存缓冲 + 后台批量落库 + 查询 API
- `tracer`    trace 上下文（contextvar + AgentMessage.payload 双通道）与埋点入口
- `middleware` ASGI 入口：为每个 HTTP 请求开 trace 并回写 `X-Trace-Id`

典型用法::

    from app import chainlog

    chainlog.info("routing", "supervisor", "route.decision", data={"to": "build"})
    with chainlog.span("agent", "supervisor", "route", data={"to": "build"}):
        ...

    payload = chainlog.attach(payload)   # 随 AgentMessage 透传给子 Agent
"""

from . import db
from .middleware import ChainLogMiddleware
from .store import (
    LEVELS,
    STAGES,
    STAGE_LABELS,
    ChainLogStore,
    new_entry_id,
    new_trace_id,
)
from .tracer import (
    TRACE_PAYLOAD_KEY,
    attach,
    bind,
    bind_from_payload,
    current,
    debug,
    error,
    info,
    log,
    mirror_event,
    new_context,
    payload_trace,
    reset,
    set_context,
    set_fields,
    span,
    store,
    trace_id,
    warning,
)

__all__ = [
    "ChainLogMiddleware", "ChainLogStore", "LEVELS", "STAGES", "STAGE_LABELS",
    "TRACE_PAYLOAD_KEY", "attach", "bind", "bind_from_payload", "current", "db",
    "debug", "error", "info", "log", "mirror_event", "new_context", "new_entry_id",
    "new_trace_id", "payload_trace", "reset", "set_context", "set_fields", "span",
    "store", "trace_id", "warning",
]
