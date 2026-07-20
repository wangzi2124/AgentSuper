"""
System monitoring: request logging, model call tracking, usage stats.
"""
import time
import logging
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)

_stats_lock = Lock()
_stats = {
    "requests_total": 0,
    "requests_by_path": defaultdict(int),
    "requests_by_status": defaultdict(int),
    "model_calls_total": 0,
    "model_calls_by_model": defaultdict(int),
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_duration_ms": 0,
    "tool_rounds_total": 0,
}


def record_request(method: str, path: str, status: int, duration_ms: float):
    """记录一次 HTTP 请求的统计信息（路径、状态码、耗时）。"""
    with _stats_lock:
        _stats["requests_total"] += 1
        _stats["requests_by_path"][f"{method} {path}"] += 1
        _stats["requests_by_status"][status] += 1


def record_model_call(
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: float = 0,
    tool_rounds: int = 0,
):
    """记录一次模型调用的统计信息（模型名、token 数、耗时、工具调用轮数）。"""
    with _stats_lock:
        _stats["model_calls_total"] += 1
        _stats["model_calls_by_model"][model] += 1
        _stats["total_prompt_tokens"] += prompt_tokens
        _stats["total_completion_tokens"] += completion_tokens
        _stats["total_duration_ms"] += duration_ms
        _stats["tool_rounds_total"] += tool_rounds


def get_stats() -> dict:
    """获取当前累积的请求和模型调用统计，返回格式化字典。"""
    with _stats_lock:
        calls = _stats["model_calls_total"]
        return {
            "requests": {
                "total": _stats["requests_total"],
                "by_path": dict(_stats["requests_by_path"]),
                "by_status": dict(_stats["requests_by_status"]),
            },
            "model_calls": {
                "total": calls,
                "by_model": dict(_stats["model_calls_by_model"]),
                "total_prompt_tokens": _stats["total_prompt_tokens"],
                "total_completion_tokens": _stats["total_completion_tokens"],
                "total_duration_ms": _stats["total_duration_ms"],
                "avg_duration_ms": round(_stats["total_duration_ms"] / calls, 1) if calls else 0,
                "tool_rounds_total": _stats["tool_rounds_total"],
                "avg_tool_rounds": round(_stats["tool_rounds_total"] / calls, 1) if calls else 0,
            },
        }


class RequestLogMiddleware:
    """ASGI 中间件，记录每个 HTTP 请求的方法、路径、状态码和耗时。"""

    def __init__(self, app):
        """初始化中间件，持有 ASGI 应用实例。"""
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import time as tmod

        start = tmod.time()
        method = scope.get("method", "?")
        path = scope.get("path", "?")

        status_sent = [None]

        async def send_wrapper(message):
            """拦截 HTTP 响应开始消息，提取状态码。"""
            if message["type"] == "http.response.start":
                status_sent[0] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            dur = (tmod.time() - start) * 1000
            status = status_sent[0] or 0
            record_request(method, path, status, dur)
            logger.info(
                "%s %s → %s (%.0fms)", method, path, status, dur
            )
