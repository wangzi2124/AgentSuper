"""全链路日志的 ASGI 入口中间件。

为每个 HTTP 请求开启一条 trace（`http.request` / `http.response` 节点），并：

- 透传上游 trace id：请求头 `X-Trace-Id` 存在则沿用（前端可自行串联多跳调用），
  否则新生成；
- 回写响应头 `X-Trace-Id`，让前端在报错/日志里能直接定位到这条链路；
- 把 user id 记进上下文，供 `app/api/logs.py` 做按用户隔离查询。

contextvar 绑定在**本请求的 task 上下文**上，因此 endpoint 及其
`asyncio.create_task` 派生的子任务都能读到同一 trace；而 AgentBus 的长驻事件
循环 task 拿不到（它启动于应用初始化），由 `AgentBus._dispatch` 从消息 payload
补绑（见 `app/chainlog/tracer.py` 顶部说明）。
"""

from __future__ import annotations

import time
from typing import Optional

from . import tracer

# 不打链路日志的路径（高频轮询/健康检查，避免噪音淹没真实链路）
_SKIP_PREFIXES = ("/api/logs", "/health", "/api/monitor/stats", "/docs", "/openapi")


class ChainLogMiddleware:
    """ASGI 中间件：建立请求级 trace 上下文并记录收发节点。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from app.config import settings

        if not getattr(settings, "chain_log_enabled", True):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        ctx = tracer.new_context(
            trace_id=(headers.get("x-trace-id") or "").strip() or None,
            method=method,
            path=path[:512],
            user_id=(headers.get("x-user-id") or "").strip(),
        )
        started = time.monotonic()
        status_holder = {"status": 0, "error": ""}
        ctx_token = tracer.set_context(ctx)

        tracer.info(
            "http", "http", "http.request",
            message=f"{method} {path}",
            data={"method": method, "path": path, "client": _client(scope)},
        )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message.get("status", 0)
                headers_out = list(message.get("headers") or [])
                headers_out.append(
                    (b"x-trace-id", str(ctx["trace_id"]).encode("latin-1"))
                )
                message = {**message, "headers": headers_out}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001
            status_holder["error"] = str(exc)
            status_holder["status"] = 500
            tracer.error(
                "http", "http", "http.error",
                message=f"{method} {path} 处理失败: {exc}",
                data={"method": method, "path": path, "error": str(exc),
                      "error_type": type(exc).__name__},
                duration_ms=_elapsed_ms(started),
            )
            raise
        finally:
            # 必须先记 http.response 再 reset —— 反过来会让收尾节点脱离本请求的
            # contextvar 上下文，被自动分配一条新 trace，前端时间线就断成两条。
            status = status_holder["status"]
            level = "INFO" if 200 <= status < 400 else (
                "ERROR" if status >= 500 else "WARNING"
            )
            tracer.log(
                level, "http", "http", "http.response",
                message=f"{method} {path} → {status}",
                data={
                    "method": method, "path": path, "status": status,
                    "error": status_holder["error"],
                },
                duration_ms=_elapsed_ms(started),
            )
            tracer.reset(ctx_token)


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _client(scope) -> str:
    client = scope.get("client")
    if not client:
        return ""
    try:
        return f"{client[0]}:{client[1]}"
    except Exception:  # noqa: BLE001
        return ""


def install(app) -> Optional[object]:
    """便捷入口：给 FastAPI 实例挂上中间件（main.py lifespan 内调用）。"""
    app.add_middleware(ChainLogMiddleware)  # type: ignore[arg-type]
    return app
