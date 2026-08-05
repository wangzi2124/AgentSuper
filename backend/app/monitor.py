"""
System monitoring: request logging, model call tracking, usage stats.

Stats are persisted to data/monitor_stats.json so that restarts do not lose
accumulated usage data (the model platform bill is the source of truth, but
this gives local visibility into every LLM call, including summarization,
compaction, supervisor decompose/synthesize, and sub-agent calls).
"""
import json
import time
import logging
from collections import defaultdict
from pathlib import Path
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
    "tool_calls_total": 0,
}

_STATS_FILE = Path(__file__).resolve().parents[1] / "data" / "monitor_stats.json"

# 落盘节流：两次持久化之间至少间隔秒数，避免每次请求/每次 LLM 调用都同步写盘阻塞事件循环
_SAVE_INTERVAL_SECONDS = 5.0
_last_save = 0.0


def _load_persisted():
    """从磁盘加载历史统计（若存在）。"""
    global _stats
    try:
        if _STATS_FILE.exists():
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("requests_by_path", "requests_by_status", "model_calls_by_model"):
                if key in data:
                    data[key] = defaultdict(int, data[key])
            _stats.update(data)
            logger.info("Loaded persisted monitor stats from %s", _STATS_FILE)
    except Exception as e:
        logger.warning("Failed to load persisted monitor stats: %s", e)


def _save_persisted():
    """将当前统计写入磁盘（节流：每 _SAVE_INTERVAL_SECONDS 最多一次）。

    节流判断与 _last_save 更新放入锁内（check-then-act 原子化），
    并发请求不会同时通过判断导致短时间多次写盘；快照也在锁内生成，
    文件 IO 在锁外执行以免阻塞统计更新。
    """
    global _last_save
    with _stats_lock:
        now = time.time()
        if now - _last_save < _SAVE_INTERVAL_SECONDS:
            return
        _last_save = now
        snapshot = {
            k: (dict(v) if isinstance(v, defaultdict) else v)
            for k, v in _stats.items()
        }
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        tmp.replace(_STATS_FILE)
    except Exception as e:
        logger.warning("Failed to persist monitor stats: %s", e)


_load_persisted()


def record_request(method: str, path: str, status: int, duration_ms: float):
    """记录一次 HTTP 请求的统计信息（路径、状态码、耗时）。"""
    with _stats_lock:
        _stats["requests_total"] += 1
        _stats["requests_by_path"][f"{method} {path}"] += 1
        _stats["requests_by_status"][status] += 1
    _save_persisted()


def record_model_call(
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: float = 0,
    tool_rounds: int = 0,
    tool_calls: int = 0,
):
    """记录一次模型调用的统计信息（模型名、token 数、耗时、工具调用轮数与次数）。"""
    with _stats_lock:
        _stats["model_calls_total"] += 1
        _stats["model_calls_by_model"][model] += 1
        _stats["total_prompt_tokens"] += prompt_tokens
        _stats["total_completion_tokens"] += completion_tokens
        _stats["total_duration_ms"] += duration_ms
        _stats["tool_rounds_total"] += tool_rounds
        _stats["tool_calls_total"] += tool_calls
    _save_persisted()


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
                "tool_calls_total": _stats["tool_calls_total"],
                "avg_tool_calls": round(_stats["tool_calls_total"] / calls, 1) if calls else 0,
            },
        }


def reset_stats():
    """清空统计并删除持久化文件（用于调试）。"""
    global _stats
    with _stats_lock:
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
            "tool_calls_total": 0,
        }
    try:
        _STATS_FILE.unlink(missing_ok=True)
    except Exception:
        pass


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
