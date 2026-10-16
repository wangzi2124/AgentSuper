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
    "total_reasoning_tokens": 0,
    "total_cache_read": 0,
    "total_cache_write": 0,
    "total_duration_ms": 0,
    "total_cost": 0.0,
    "tool_rounds_total": 0,
    "tool_calls_total": 0,
    # per-model 分解
    "prompt_tokens_by_model": defaultdict(int),
    "completion_tokens_by_model": defaultdict(int),
    "reasoning_tokens_by_model": defaultdict(int),
    "cache_read_by_model": defaultdict(int),
    "cache_write_by_model": defaultdict(int),
    "cost_by_model": defaultdict(float),
}

_STATS_FILE = Path(__file__).resolve().parents[1] / "data" / "monitor_stats.json"

# 落盘节流：两次持久化之间至少间隔秒数，避免每次请求/每次 LLM 调用都同步写盘阻塞事件循环
_SAVE_INTERVAL_SECONDS = 5.0
_last_save = 0.0

# ── 时间窗指标 ring buffer（data/metrics.jsonl）──
# 累积统计（monitor_stats.json）只有「生命周期总量」，看不到「最近 24h」。
# 这里把每次模型调用/请求按时间写成 JSONL 追加日志，读取时按时间窗聚合；
# 落盘与文件裁剪都做节流，避免高频写盘阻塞事件循环。
_METRICS_FILE = Path(__file__).resolve().parents[1] / "data" / "metrics.jsonl"
# 只保留最近 N 小时的事件（时间裁剪），读取窗口不超过该值
_METRICS_RETENTION_HOURS = 24.0
# 文件裁剪（读全量→重写）间隔：避免每次 flush 都重写整个文件
_METRICS_PRUNE_INTERVAL_SECONDS = 300.0
_metrics_buffer: list = []
_last_metrics_flush = 0.0
_last_metrics_prune = 0.0


def _load_persisted():
    """从磁盘加载历史统计（若存在）。"""
    global _stats
    try:
        if _STATS_FILE.exists():
            with open(_STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("requests_by_path", "requests_by_status", "model_calls_by_model",
                        "prompt_tokens_by_model", "completion_tokens_by_model",
                        "reasoning_tokens_by_model", "cache_read_by_model", "cache_write_by_model"):
                if key in data:
                    data[key] = defaultdict(int, data[key])
            for key in ("cost_by_model",):
                if key in data:
                    data[key] = defaultdict(float, data[key])
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


def _record_metric(kind: str, **fields):
    """把一条带时间戳的事件追加到内存缓冲，并按节流 flush 到 metrics.jsonl。"""
    with _stats_lock:
        _metrics_buffer.append({"ts": time.time(), "kind": kind, **fields})
    _flush_metrics()


def _flush_metrics(force: bool = False):
    """把缓冲的事件批量追加到 JSONL（节流：距上次 flush 至少 _SAVE_INTERVAL_SECONDS）。

    force=True 时忽略节流立即写盘（读取窗口前调用，保证窗口完整）。
    """
    global _last_metrics_flush
    with _stats_lock:
        if not _metrics_buffer:
            return
        now = time.time()
        if not force and (now - _last_metrics_flush) < _SAVE_INTERVAL_SECONDS:
            return
        _last_metrics_flush = now
        rows = _metrics_buffer[:]
        _metrics_buffer.clear()
    try:
        _METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_METRICS_FILE, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Failed to append metrics: %s", e)
        return
    _prune_metrics()


def _prune_metrics():
    """时间裁剪 metrics.jsonl：只保留最近 _METRICS_RETENTION_HOURS 小时的事件。

    读全量→重写成本随文件增长，故用 _METRICS_PRUNE_INTERVAL_SECONDS 节流。
    """
    global _last_metrics_prune
    now = time.time()
    if (now - _last_metrics_prune) < _METRICS_PRUNE_INTERVAL_SECONDS:
        return
    _last_metrics_prune = now
    cutoff = now - _METRICS_RETENTION_HOURS * 3600
    try:
        if not _METRICS_FILE.exists():
            return
        kept: list = []
        with open(_METRICS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    r = json.loads(s)
                except Exception:
                    continue
                if float(r.get("ts") or 0) >= cutoff:
                    kept.append(s)
        tmp = _METRICS_FILE.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            if kept:
                f.write("\n".join(kept) + "\n")
        tmp.replace(_METRICS_FILE)
    except Exception as e:
        logger.warning("Failed to prune metrics: %s", e)


_load_persisted()


def record_request(method: str, path: str, status: int, duration_ms: float):
    """记录一次 HTTP 请求的统计信息（路径、状态码、耗时）。"""
    with _stats_lock:
        _stats["requests_total"] += 1
        _stats["requests_by_path"][f"{method} {path}"] += 1
        _stats["requests_by_status"][status] += 1
    _record_metric("request", path=f"{method} {path}", status=status,
                   duration_ms=round(duration_ms, 1))
    _save_persisted()


def record_model_call(
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: float = 0,
    tool_rounds: int = 0,
    tool_calls: int = 0,
    reasoning_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cost: float = 0.0,
):
    from observability.trace_log import trace as _tr  # [token trace v7]
    _tr("monitor.record_model_call", model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, duration_ms=duration_ms, tool_rounds=tool_rounds, tool_calls=tool_calls)
    """记录一次模型调用的统计信息（模型名、token 数、耗时、工具调用轮数与次数）。"""
    with _stats_lock:
        _stats["model_calls_total"] += 1
        _stats["model_calls_by_model"][model] += 1
        _stats["total_prompt_tokens"] += prompt_tokens
        _stats["total_completion_tokens"] += completion_tokens
        _stats["total_reasoning_tokens"] += reasoning_tokens
        _stats["total_cache_read"] += cache_read
        _stats["total_cache_write"] += cache_write
        _stats["total_duration_ms"] += duration_ms
        _stats["total_cost"] += float(cost)
        _stats["prompt_tokens_by_model"][model] += prompt_tokens
        _stats["completion_tokens_by_model"][model] += completion_tokens
        _stats["reasoning_tokens_by_model"][model] += reasoning_tokens
        _stats["cache_read_by_model"][model] += cache_read
        _stats["cache_write_by_model"][model] += cache_write
        _stats["cost_by_model"][model] += float(cost)
        _stats["tool_rounds_total"] += tool_rounds
        _stats["tool_calls_total"] += tool_calls
    _record_metric(
        "model", model=model, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, reasoning_tokens=reasoning_tokens,
        cache_read=cache_read, cache_write=cache_write,
        duration_ms=round(duration_ms, 1), tool_rounds=tool_rounds,
        tool_calls=tool_calls, cost=round(float(cost), 8),
    )
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
                "total_reasoning_tokens": _stats["total_reasoning_tokens"],
                "total_cache_read": _stats["total_cache_read"],
                "total_cache_write": _stats["total_cache_write"],
                "total_duration_ms": _stats["total_duration_ms"],
                "avg_duration_ms": round(_stats["total_duration_ms"] / calls, 1) if calls else 0,
                "tool_rounds_total": _stats["tool_rounds_total"],
                "avg_tool_rounds": round(_stats["tool_rounds_total"] / calls, 1) if calls else 0,
                "tool_calls_total": _stats["tool_calls_total"],
                "avg_tool_calls": round(_stats["tool_calls_total"] / calls, 1) if calls else 0,
                "prompt_tokens_by_model": dict(_stats["prompt_tokens_by_model"]),
                "completion_tokens_by_model": dict(_stats["completion_tokens_by_model"]),
                "reasoning_tokens_by_model": dict(_stats["reasoning_tokens_by_model"]),
                "cache_read_by_model": dict(_stats["cache_read_by_model"]),
                "cache_write_by_model": dict(_stats["cache_write_by_model"]),
                "cost_by_model": dict(_stats["cost_by_model"]),
            },
            "total_cost": round(_stats["total_cost"], 6),
        }


def get_recent_stats(window_hours: float = 24.0) -> dict:
    """聚合最近 window_hours 小时的事件（ring buffer → metrics.jsonl）。

    窗口被夹在 (0, _METRICS_RETENTION_HOURS]；读取前先强制 flush，把未落盘的
    缓冲一并计入，保证窗口完整。
    """
    try:
        window = float(window_hours)
    except (TypeError, ValueError):
        window = 24.0
    window = max(0.1, min(window, _METRICS_RETENTION_HOURS))
    _flush_metrics(force=True)
    cutoff = time.time() - window * 3600
    calls = 0
    by_model: dict = defaultdict(int)
    prompt_tokens = completion_tokens = reasoning_tokens = 0
    cache_read = cache_write = 0
    duration_ms = 0.0
    cost = 0.0
    requests = 0
    by_status: dict = defaultdict(int)
    try:
        if _METRICS_FILE.exists():
            with open(_METRICS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        r = json.loads(s)
                    except Exception:
                        continue
                    if float(r.get("ts") or 0) < cutoff:
                        continue
                    kind = r.get("kind")
                    if kind == "model":
                        calls += 1
                        by_model[r.get("model") or "?"] += 1
                        prompt_tokens += int(r.get("prompt_tokens") or 0)
                        completion_tokens += int(r.get("completion_tokens") or 0)
                        reasoning_tokens += int(r.get("reasoning_tokens") or 0)
                        cache_read += int(r.get("cache_read") or 0)
                        cache_write += int(r.get("cache_write") or 0)
                        duration_ms += float(r.get("duration_ms") or 0)
                        cost += float(r.get("cost") or 0)
                    elif kind == "request":
                        requests += 1
                        by_status[int(r.get("status") or 0)] += 1
    except Exception as e:
        logger.warning("Failed to read recent metrics: %s", e)
    return {
        "window_hours": window,
        "model_calls": {
            "total": calls,
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "total_reasoning_tokens": reasoning_tokens,
            "total_cache_read": cache_read,
            "total_cache_write": cache_write,
            "total_duration_ms": round(duration_ms, 1),
            "avg_duration_ms": round(duration_ms / calls, 1) if calls else 0,
            "total_cost": round(cost, 6),
            "by_model": dict(by_model),
        },
        "requests": {"total": requests, "by_status": dict(by_status)},
    }


def reset_stats():
    """清空统计并删除持久化文件（累积统计 + 时间窗指标，用于调试）。"""
    global _stats, _metrics_buffer
    with _stats_lock:
        _stats = {
            "requests_total": 0,
            "requests_by_path": defaultdict(int),
            "requests_by_status": defaultdict(int),
            "model_calls_total": 0,
            "model_calls_by_model": defaultdict(int),
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_reasoning_tokens": 0,
            "total_cache_read": 0,
            "total_cache_write": 0,
            "total_duration_ms": 0,
            "total_cost": 0.0,
            "tool_rounds_total": 0,
            "tool_calls_total": 0,
            "prompt_tokens_by_model": defaultdict(int),
            "completion_tokens_by_model": defaultdict(int),
            "reasoning_tokens_by_model": defaultdict(int),
            "cache_read_by_model": defaultdict(int),
            "cache_write_by_model": defaultdict(int),
            "cost_by_model": defaultdict(float),
        }
        _metrics_buffer = []
    try:
        _STATS_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        _METRICS_FILE.unlink(missing_ok=True)
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
