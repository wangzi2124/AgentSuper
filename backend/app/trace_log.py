# -*- coding: utf-8 -*-
"""Token 全流程追踪日志 [token 优化 v7]。

从「问助手」请求进入 Agent 到每次 LLM 返回，把关键节点以 JSON Lines 追加到
logs/token_trace_YYYYMMDD.jsonl，供 token_patch/analyze_token_trace.py 分析。

事件一览（event 字段）:
  graph.entry_ready       入口上下文就绪（system + 历史 + 检索注入后、首轮 LLM 前）
  graph.round_start       工具循环每轮开始时（prune 前）的上下文规模
  graph.pre_compact       本轮 prune 后、压缩判断前（threshold 附在 payload）
  graph.round_ready       本轮 truncate 后、发送 LLM 前的上下文规模
  llm.usage               每次 LLM 返回的实际 usage（pt/ct/dur/model/where，前缀缓存命中/未命中 cache_hit/cache_miss）
  monitor.record_model_call  monitor.py 记账总入口（兜底，防漏）
  graph.finish            整个请求结束（rounds / tool_calls / 总耗时）

batch 字段：每次 entry_ready 视为新请求批次，自动 +1，便于按请求分组分析。
"""
import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_batch = {"n": 0}
_path: Path | None = None


def _log_path() -> Path:
    global _path
    if _path is None:
        base = os.environ.get("AGENTSUPER_LOG_DIR", "logs")
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        _path = d / f"token_trace_{time.strftime('%Y%m%d')}.jsonl"
    return _path


def trace(event: str, **payload) -> None:
    """写一条 JSON Line；任何失败静默忽略，绝不影响主流程。"""
    try:
        if event == "graph.entry_ready":
            _batch["n"] += 1
        rec = {"ts": round(time.time(), 3), "batch": _batch["n"], "event": event}
        rec.update(payload)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def trace_messages(event: str, messages: list, **payload) -> None:
    """估算消息列表 token 并写一条 trace（估算失败则只记条数）。"""
    try:
        from app.context.token_counter import estimate_tokens_messages
        payload["tokens"] = estimate_tokens_messages(messages)
    except Exception:
        payload["tokens"] = -1
    payload["msg_count"] = len(messages)
    trace(event, **payload)
