# -*- coding: utf-8 -*-
"""monitor.py 全量用例：请求/模型调用统计、落盘节流、持久化加载、重置、
RequestLogMiddleware ASGI 中间件（http/websocket/异常路径）。

运行：pytest tests/test_monitor_stats.py
"""
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.trace_log as tl
import app.monitor as mon
from app.monitor import RequestLogMiddleware, get_stats


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    """隔离全局状态：统计文件指到 tmp、trace 目录指到 tmp、每次重置计数。"""
    tl._path = None
    monkeypatch.setenv("AGENTSUPER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mon, "_STATS_FILE", tmp_path / "monitor_stats.json")
    monkeypatch.setattr(mon, "_last_save", 0.0)
    mon.reset_stats()


def test_record_request_and_persist(tmp_path):
    mon.record_request("GET", "/api/chat", 200, 12.5)
    mon.record_request("GET", "/api/chat", 200, 8.0)
    mon.record_request("POST", "/api/chat/multi-agent", 500, 40.1)
    stats = get_stats()
    assert stats["requests"]["total"] == 3
    assert stats["requests"]["by_path"] == {
        "GET /api/chat": 2,
        "POST /api/chat/multi-agent": 1,
    }
    assert stats["requests"]["by_status"] == {200: 2, 500: 1}
    # 落盘
    assert (tmp_path / "monitor_stats.json").exists()


def test_record_model_call_and_averages():
    mon.record_model_call("deepseek/x", prompt_tokens=100, completion_tokens=50, duration_ms=200, tool_rounds=2, tool_calls=5)
    mon.record_model_call("deepseek/x", prompt_tokens=300, completion_tokens=50, duration_ms=400, tool_rounds=1, tool_calls=2)
    stats = get_stats()["model_calls"]
    assert stats["total"] == 2
    assert stats["by_model"] == {"deepseek/x": 2}
    assert stats["total_prompt_tokens"] == 400
    assert stats["total_completion_tokens"] == 100
    assert stats["total_duration_ms"] == 600
    assert stats["avg_duration_ms"] == 300.0
    assert stats["tool_rounds_total"] == 3
    assert stats["avg_tool_rounds"] == 1.5
    assert stats["tool_calls_total"] == 7
    assert stats["avg_tool_calls"] == 3.5


def test_get_stats_zero_calls():
    stats = get_stats()["model_calls"]
    assert stats["total"] == 0
    assert stats["avg_duration_ms"] == 0  # 不除零
    assert stats["avg_tool_rounds"] == 0


def test_save_throttle(tmp_path):
    mon._last_save = time.time() + 100
    mon._save_persisted()
    assert not (tmp_path / "monitor_stats.json").exists()
    mon._last_save = 0.0
    mon._save_persisted()
    assert (tmp_path / "monitor_stats.json").exists()


def test_load_persisted(tmp_path):
    (tmp_path / "monitor_stats.json").write_text(
        json.dumps({
            "requests_total": 7,
            "requests_by_path": {"GET /x": 2},
            "requests_by_status": {200: 2},
            "model_calls_total": 3,
            "model_calls_by_model": {"m": 3},
            "total_prompt_tokens": 99,
        }),
        encoding="utf-8",
    )
    mon._load_persisted()
    assert mon._stats["requests_total"] == 7
    assert mon._stats["requests_by_path"] == {"GET /x": 2}
    assert mon._stats["model_calls_total"] == 3


def test_load_persisted_bad_json(tmp_path, caplog):
    (tmp_path / "monitor_stats.json").write_text("{bad json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        mon._load_persisted()
    assert "Failed to load persisted monitor stats" in caplog.text


def test_reset_stats(tmp_path):
    mon.record_request("GET", "/x", 200, 1)
    mon.reset_stats()
    assert get_stats()["requests"]["total"] == 0
    assert not (tmp_path / "monitor_stats.json").exists()


async def test_middleware_http_records():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 201})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = []

    async def send(message):
        sent.append(message)

    mw = RequestLogMiddleware(app)
    await mw({"type": "http", "method": "GET", "path": "/api/x"}, None, send)
    stats = get_stats()
    assert stats["requests"]["total"] == 1
    assert stats["requests"]["by_status"] == {201: 1}
    assert stats["requests"]["by_path"] == {"GET /api/x": 1}
    assert sent[0]["status"] == 201


async def test_middleware_non_http_passthrough():
    called = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    mw = RequestLogMiddleware(app)
    await mw({"type": "websocket", "path": "/ws"}, None, None)
    assert called == ["websocket"]
    assert get_stats()["requests"]["total"] == 0  # 不记录


async def test_middleware_exception_still_records():
    async def bad_app(scope, receive, send):
        raise RuntimeError("boom")

    mw = RequestLogMiddleware(bad_app)
    with pytest.raises(RuntimeError):
        await mw({"type": "http", "method": "POST", "path": "/api/boom"}, None, None)
    stats = get_stats()
    assert stats["requests"]["total"] == 1
    assert stats["requests"]["by_status"] == {0: 1}  # 无响应状态 → 0
    assert stats["requests"]["by_path"] == {"POST /api/boom": 1}