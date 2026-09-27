"""全链路日志的埋点上下文与记录入口。

核心是**一条 trace 贯穿整条链路**：HTTP 入口生成 `trace_id` → 随
`AgentMessage.payload["_chain_trace"]` 透传到 supervisor / 每个子 Agent →
工具调用、LLM 调用、权限审批、消息落库各写一行，同一 `trace_id` + 单调 `seq`
把「一次请求发生了什么」串成可下钻的时间线。

为什么用 contextvar + payload 双通道
------------------------------------
- contextvar：同一次请求内的同步/异步代码共享上下文，零显式传参；
- payload：AgentBus 的事件循环是**启动期**创建的长驻 task，contextvar 不会从
  请求侧继承（`asyncio.create_task` 复制的是创建者 task 的上下文）。所以 trace
  上下文必须随 `AgentMessage.payload` 走消息本身，才能跨到子 Agent。
  `AgentBus._dispatch` 收到消息后用 `bind_from_payload` 重新绑定，之后子 Agent
  内部（含 `tool_task` 二次委派）就都能读到了。

所有 `log()` / `span()` 都是**永不抛异常**的：日志故障不允许影响业务。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator, Optional

from app.config import settings

from .store import ChainLogStore, new_entry_id, new_trace_id

# payload 中承载 trace 上下文的键（与 `_event_queue` 同为「下划线前缀」内部约定）
TRACE_PAYLOAD_KEY = "_chain_trace"

_ctx: ContextVar[Optional[dict]] = ContextVar("chainlog_ctx", default=None)

# trace 内 seq 分配器：与 contextvar 一起按 context 隔离，无需加锁；
# 但 contextvar 可能在同一 context 被复用（重入 span），故用锁兜底递增。
# 计数器按 trace 累积，需有上界：超过 _SEQ_CACHE_MAX 时丢弃最旧的一半
# （dict 保序，插入序 ≈ 首次出现序）。已结束的 trace 重新出现时 seq 会重新计数，
# 但排序以 ts 为主、seq 为辅，不影响链路还原。
_seq_lock = threading.Lock()
_seq_counters: dict[str, int] = {}
_SEQ_CACHE_MAX = 20000


def store() -> ChainLogStore:
    """返回进程级单例 store。"""
    global _store
    if _store is None:
        _store = ChainLogStore()
    return _store


_store: Optional[ChainLogStore] = None


# ── 上下文 ────────────────────────────────────────────────────────────────

def new_context(
    *,
    trace_id: Optional[str] = None,
    parent_id: str = "",
    session_id: str = "",
    user_id: str = "",
    path: str = "",
    method: str = "",
) -> dict:
    """构造一个新的 trace 上下文。"""
    return {
        "trace_id": trace_id or new_trace_id(),
        "parent_id": parent_id,
        "session_id": session_id,
        "user_id": user_id,
        "path": path,
        "method": method,
    }


def current() -> dict:
    """当前 trace 上下文快照（无上下文时返回空 dict）。"""
    return dict(_ctx.get() or {})


def trace_id() -> str:
    return str((_ctx.get() or {}).get("trace_id") or "")


def payload_trace() -> dict:
    """可塞进 `AgentMessage.payload` 的 trace 上下文副本。"""
    ctx = _ctx.get()
    if not ctx:
        return {}
    return {
        "trace_id": ctx.get("trace_id", ""),
        "parent_id": ctx.get("parent_id", ""),
        "session_id": ctx.get("session_id", ""),
        "user_id": ctx.get("user_id", ""),
        "path": ctx.get("path", ""),
        "method": ctx.get("method", ""),
    }


@contextmanager
def bind(ctx: Optional[dict]) -> Iterator[dict]:
    """在上下文管理器作用域内绑定 trace 上下文。"""
    token = _ctx.set(dict(ctx or {}))
    try:
        yield _ctx.get() or {}
    finally:
        _ctx.reset(token)


def bind_from_payload(payload: Optional[dict]) -> Optional[Token]:
    """从 AgentMessage.payload 取出 trace 上下文并绑定（返回 reset token）。"""
    if not payload:
        return None
    carried = payload.get(TRACE_PAYLOAD_KEY)
    if not isinstance(carried, dict) or not carried.get("trace_id"):
        return None
    return _ctx.set(dict(carried))


def reset(token: Optional[Token]) -> None:
    if token is not None:
        try:
            _ctx.reset(token)
        except Exception:  # noqa: BLE001
            pass


def set_context(ctx: dict) -> Token:
    """直接绑定上下文（ASGI 中间件入口用），返回 reset token。"""
    return _ctx.set(dict(ctx or {}))


def set_fields(**fields: Any) -> None:
    """就地更新当前上下文（如 endpoint 解析出会话后补 session_id）。"""
    ctx = _ctx.get()
    if ctx is None:
        return
    for key, value in fields.items():
        if value not in (None, ""):
            ctx[key] = value


def attach(payload: dict) -> dict:
    """把当前 trace 上下文注入 payload（供 supervisor 转发给子 Agent）。"""
    if isinstance(payload, dict):
        carried = payload_trace()
        if carried:
            payload[TRACE_PAYLOAD_KEY] = carried
    return payload


def _next_seq(tid: str) -> int:
    with _seq_lock:
        n = _seq_counters.get(tid, 0) + 1
        if n > 1_000_000:
            n = 1
        _seq_counters[tid] = n
        if len(_seq_counters) > _SEQ_CACHE_MAX:
            for old in list(_seq_counters.keys())[: _SEQ_CACHE_MAX // 2]:
                _seq_counters.pop(old, None)
        return n


# ── 记录 ──────────────────────────────────────────────────────────────────

def log(
    level: str,
    stage: str,
    component: str,
    event: str,
    *,
    message: str = "",
    data: Optional[dict] = None,
    agent_id: str = "",
    duration_ms: Optional[float] = None,
    trace_id_override: str = "",
    parent_id_override: str = "",
) -> str:
    """记录一条链路节点。永不抛异常。返回该节点的 entry id（供 span 串联父子）。

    没有上下文时（如启动期的后台任务）自动开一条独立 trace，保证不丢日志。
    """
    try:
        if not getattr(settings, "chain_log_enabled", True):
            return ""
        ctx = _ctx.get() or {}
        tid = trace_id_override or str(ctx.get("trace_id") or "") or new_trace_id()
        entry_id = new_entry_id()
        entry = {
            "id": entry_id,
            "trace_id": tid,
            "parent_id": parent_id_override or str(ctx.get("parent_id") or ""),
            "seq": _next_seq(tid),
            "ts": int(time.time() * 1000),
            "level": (level or "INFO").upper(),
            "stage": stage or "system",
            "component": component or "",
            "event": event or "",
            "agent_id": agent_id or "",
            "session_id": str(ctx.get("session_id") or ""),
            "user_id": str(ctx.get("user_id") or ""),
            "path": str(ctx.get("path") or ""),
            "message": message or "",
            # 浅拷贝快照：span 的 start/end 共用同一个 data dict，若不拷贝，
            # 块内的后续改写会「回溯」污染已入队的 start 节点（落库时序不定）。
            "data": dict(data) if data else {},
            "duration_ms": duration_ms,
        }
        store().append(entry)
        return entry_id
    except Exception:  # noqa: BLE001
        return ""


def info(stage: str, component: str, event: str, **kw: Any) -> None:
    log("INFO", stage, component, event, **kw)


def warning(stage: str, component: str, event: str, **kw: Any) -> None:
    log("WARNING", stage, component, event, **kw)


def error(stage: str, component: str, event: str, **kw: Any) -> None:
    log("ERROR", stage, component, event, **kw)


def debug(stage: str, component: str, event: str, **kw: Any) -> None:
    log("DEBUG", stage, component, event, **kw)


@contextmanager
def span(
    stage: str,
    component: str,
    event: str,
    *,
    message: str = "",
    data: Optional[dict] = None,
    agent_id: str = "",
) -> Iterator[dict]:
    """记录一个「开始 + 结束（含耗时/异常）」的 span 节点。

    用法::

        with span("agent", "supervisor", "route", data={"to": target}) as rec:
            ...
            rec["routed"] = True

    `rec` 就是 `data` 字典本身，可在块内补充字段（结束后一并落库）。异常时记
    ERROR 并把 `error`/`error_type` 写进 data 后**原样抛出**（不吞异常）。
    """
    started = time.monotonic()
    payload: dict = dict(data or {})
    start_id = log(
        "INFO", stage, component, f"{event}.start", message=message,
        data=payload or None, agent_id=agent_id,
    )
    try:
        yield payload
    except BaseException as exc:  # noqa: BLE001
        payload["error"] = str(exc)
        payload["error_type"] = type(exc).__name__
        log(
            "ERROR", stage, component, f"{event}.error", message=str(exc),
            data=payload, agent_id=agent_id,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            parent_id_override=start_id,
        )
        raise
    log(
        "INFO", stage, component, f"{event}.end",
        data=payload or None, agent_id=agent_id,
        duration_ms=round((time.monotonic() - started) * 1000, 1),
        parent_id_override=start_id,
    )


# ── SSE 事件镜像（子 Agent 细粒度事件的唯一收敛点）─────────────────────

# graph 步骤事件 → 链路阶段（tool_* 归入 tool，其余归 agent）
_STAGE_BY_EVENT = {
    "tool_start": "tool",
    "tool_end": "tool",
    "tool_heartbeat": "tool",
    "step_start": "agent",
    "step_end": "agent",
}


def mirror_event(event: dict, component: str = "agent") -> None:
    """把 `AgentEventCollector` 收到的一条子 Agent 事件镜像为链路节点。

    这是子 Agent 工具/步骤明细的**唯一埋点入口** —— 所有子 Agent（build/rag/
    explore/plan/web_search）的事件都先流经 collector，因此一处接入即可覆盖
    全链路工具调用，且不必改动任何 Agent 实现。
    """
    try:
        if not getattr(settings, "chain_log_enabled", True):
            return
        et = str(event.get("type") or "")
        agent_id = str(event.get("agent_id") or "")
        if et == "agent_start":
            info(
                "agent", component, "agent.start", agent_id=agent_id,
                message=str(event.get("agent_name") or agent_id),
                data={"agent_name": event.get("agent_name"),
                      "avatar": event.get("agent_avatar")},
            )
            return
        if et == "agent_done":
            content = str(event.get("content") or "")
            info(
                "agent", component, "agent.done", agent_id=agent_id,
                message=f"{agent_id} 完成（回答 {len(content)} 字）",
                data={"answer_chars": len(content), "preview": content[:500]},
            )
            return
        if et == "agent_error":
            error(
                "agent", component, "agent.error", agent_id=agent_id,
                message=str(event.get("error") or "agent error"),
                data={"error": event.get("error")},
            )
            return
        if et == "permission_request":
            info(
                "permission", component, "permission.request", agent_id=agent_id,
                message=f"等待审批: {event.get('operation')} {event.get('path')}",
                data={
                    "request_id": event.get("request_id"),
                    "path": event.get("path"),
                    "operation": event.get("operation"),
                    "tool_name": event.get("tool_name"),
                    "tool_args": event.get("tool_args"),
                    "created_at": event.get("created_at"),
                    "expires_at": event.get("expires_at"),
                },
            )
            return
        if et != "agent_step":
            return
        step = event.get("step") or {}
        step_type = str(step.get("type") or "")
        stage = _STAGE_BY_EVENT.get(step_type, "agent")
        # 工具调用明细可单独关闭（高频，写入量大）
        if stage == "tool" and not getattr(settings, "chain_log_tool_calls", True):
            return
        name = str(step.get("name") or step.get("step_id") or step_type or "step")
        status = str(step.get("status") or "")
        level = "INFO"
        if status in ("error", "failed"):
            level = "ERROR"
        elif status == "running" and step_type == "tool_heartbeat":
            level = "DEBUG"
        data = {
            "step_id": step.get("step_id"),
            "step_type": step_type,
            "status": status,
            "tool_name": step.get("tool_name"),
            "tool_args": step.get("tool_args"),
            "detail": step.get("detail"),
        }
        log(
            level, stage, component, name, agent_id=agent_id,
            message=str(step.get("detail") or name), data=data,
            duration_ms=step.get("duration_ms"),
        )
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "TRACE_PAYLOAD_KEY", "attach", "bind", "bind_from_payload", "current",
    "debug", "error", "info", "log", "mirror_event", "new_context", "payload_trace",
    "reset", "set_context", "set_fields", "span", "store", "trace_id", "warning",
]
