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
    with _stats_lock:
        _stats["model_calls_total"] += 1
        _stats["model_calls_by_model"][model] += 1
        _stats["total_prompt_tokens"] += prompt_tokens
        _stats["total_completion_tokens"] += completion_tokens
        _stats["total_duration_ms"] += duration_ms
        _stats["tool_rounds_total"] += tool_rounds


def get_stats() -> dict:
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
    def __init__(self, app):
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
