# -*- coding: utf-8 -*-
"""小型工具模块全量用例：tool_dedup / task_bridge / trace_log / prompt_log /
session.history / stream_events（扩展，补齐 test_stream_events.py 未覆盖分支）。

运行：pytest tests/test_small_modules.py
"""
import asyncio
import json
import os
import re
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.prompt_log as prompt_log
import app.trace_log as trace_log
from app.context.tool_dedup import (
    NON_IDEMPOTENT_TOOLS,
    ToolResultDedup,
    _make_dedup_key,
)
from app.session import history
from app.session import task_bridge
from app.agent.stream_events import (
    AGENT_AVATARS,
    AGENT_LABELS,
    AgentEventCollector,
    TaggedEventQueue,
    agent_meta,
    emit,
    step_event,
    unwrap_tagged,
)


# ── tool_dedup ─────────────────────────────────────────────────────────────

def test_dedup_key_sort_agnostic():
    a = _make_dedup_key("tool_ls", {"path": "/x", "limit": 10})
    b = _make_dedup_key("tool_ls", {"limit": 10, "path": "/x"})
    assert a == b
    assert a != _make_dedup_key("tool_ls", {"path": "/y", "limit": 10})


def test_dedup_key_fallback_on_unserializable():
    # set 值无法 json.dumps → 回退 str(sorted(items))
    k = _make_dedup_key("tool", {"items": {1, 2}})
    assert k
    k2 = _make_dedup_key("tool", {"items": {2, 1}})
    assert k == k2  # sorted 后顺序稳定


def test_should_dedup_defaults():
    d = ToolResultDedup()
    assert d.should_dedup("tool_read_file") is True
    assert d.should_dedup("tool_execute") is False
    assert d.should_dedup("tool_http_request") is False
    assert d.should_dedup("plugin_weather_tool_get_weather") is False
    assert "tool_execute" in NON_IDEMPOTENT_TOOLS


def test_should_dedup_custom_skip():
    d = ToolResultDedup(skip_names={"tool_ls"})
    assert d.should_dedup("tool_ls") is False
    assert d.should_dedup("tool_read_file") is True


def test_dedup_get_set_stats_clear():
    d = ToolResultDedup()
    assert d.get("nope") is None
    assert d.stats()["misses"] == 1
    d.set("k", "v")
    assert d.get("k") == "v"
    s = d.stats()
    assert s["hits"] == 1 and s["cached_entries"] == 1 and s["total_calls"] == 2
    d.clear()
    assert d.stats()["hits"] == 0 and d.get("k") is None


# ── task_bridge ────────────────────────────────────────────────────────────

def test_task_bridge_register_unregister():
    task_bridge._threads.clear()
    task_bridge.register("child1", "thread-1")
    assert task_bridge.thread_for("child1") == "thread-1"
    task_bridge.unregister("child1")
    assert task_bridge.thread_for("child1") is None


def test_task_bridge_cancel_no_bus():
    task_bridge._threads.clear()
    task_bridge._bus = None
    task_bridge.register("child1", "thread-1")
    assert task_bridge.cancel("child1") is False
    assert task_bridge.thread_for("child1") is None  # 已弹掉


def test_task_bridge_cancel_success():
    task_bridge._bus = SimpleNamespace(cancel_pending=lambda tid: True)
    task_bridge._threads.clear()
    task_bridge.register("child1", "thread-1")
    assert task_bridge.cancel("child1") is True


def test_task_bridge_cancel_bus_failure_logs():
    def boom(tid):
        raise RuntimeError("bus down")
    task_bridge._bus = SimpleNamespace(cancel_pending=boom)
    task_bridge._threads.clear()
    task_bridge.register("child1", "thread-1")
    assert task_bridge.cancel("child1") is False


def test_task_bridge_cancel_children_aggregate():
    task_bridge._bus = SimpleNamespace(cancel_pending=lambda tid: True)
    task_bridge._threads.clear()
    task_bridge.register("c1", "t1")
    task_bridge.register("c2", "t2")
    task_bridge.register("c3", "t3")
    task_bridge.unregister("c3")
    assert task_bridge.cancel_children(["c1", "c2", "c3", "c4"]) == 2


# ── trace_log ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _trace_dir(monkeypatch, tmp_path):
    trace_log._path = None
    trace_log._batch["n"] = 0
    monkeypatch.setenv("AGENTSUPER_LOG_DIR", str(tmp_path / "traces"))


def test_trace_batch_and_fields(tmp_path):
    trace_log.trace("graph.entry_ready")
    trace_log.trace("llm.usage", model="m", pt=1, ct=2, cache_hit=True)
    files = list((tmp_path / "traces").glob("token_trace_*.jsonl"))
    assert len(files) == 1
    lines = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]
    assert lines[0]["batch"] == 1
    assert lines[0]["event"] == "graph.entry_ready"
    assert lines[1]["batch"] == 1  # 非 entry_ready 事件不新增 batch
    assert lines[1]["event"] == "llm.usage"
    assert lines[1]["model"] == "m"


def test_trace_non_serializable_default_str(tmp_path):
    trace_log.trace("graph.round_start", obj=object())
    files = list((tmp_path / "traces").glob("token_trace_*.jsonl"))
    lines = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]
    assert isinstance(lines[0]["obj"], str)


def test_trace_failure_silent(monkeypatch):
    def boom():
        raise OSError("cannot create dir")
    monkeypatch.setattr(trace_log, "_log_path", boom)
    trace_log.trace("anything")  # 不应抛异常


def test_trace_messages_tokens_computed(monkeypatch):
    captured = {}

    def spy(event, **payload):
        captured.update(payload)
    monkeypatch.setattr(trace_log, "trace", spy)
    trace_log.trace_messages(
        "graph.round_ready",
        [{"role": "user", "content": "hi"}],
        tool_defs=[{"name": "t1"}],
        extra="x",
    )
    assert captured["tokens"] >= 0
    assert captured["msg_count"] == 1
    assert captured["extra"] == "x"


def test_trace_messages_estimation_failure(monkeypatch):
    captured = {}

    def spy(event, **payload):
        captured.update(payload)
    monkeypatch.setattr(trace_log, "trace", spy)
    monkeypatch.setattr(
        "app.context.token_counter.estimate_tools", lambda d: (_ for _ in ()).throw(RuntimeError())
    )
    trace_log.trace_messages("graph.pre_compact", [{"role": "user", "content": "hi"}])
    assert captured["tokens"] == -1
    assert captured["msg_count"] == 1


# ── prompt_log ─────────────────────────────────────────────────────────────

def test_prompt_log_writes_jsonl(tmp_path):
    prompt_log.log_prompt("graph.llm_call", [{"role": "user", "content": "q"}], model="m", tool_count=3)
    files = list((tmp_path / "traces").glob("graph.llm_call_*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert rec["call_type"] == "graph.llm_call"
    assert rec["messages"] == [{"role": "user", "content": "q"}]
    assert rec["model"] == "m"
    assert rec["tool_count"] == 3


def test_prompt_log_failure_silent(monkeypatch):
    def boom(call_type):
        raise OSError("no dir")
    monkeypatch.setattr(prompt_log, "_log_path", boom)
    prompt_log.log_prompt("code_agent.ask_llm", [])  # 不应抛异常


# ── session.history ────────────────────────────────────────────────────────

def test_text_from_parts():
    p1 = SimpleNamespace(type="text", data={"text": "hello"})
    p2 = SimpleNamespace(type="reasoning", data={"reasoning": "think"})
    p3 = SimpleNamespace(type="text", data={"text": " world"})
    assert history.text_from_parts([p1, p3]) == "hello\n world"
    assert history.text_from_parts([p2]) == ""
    assert history.text_from_parts([p2], include_reasoning=True) == "[reasoning]\nthink\n[/reasoning]"
    # dict-style
    assert history.text_from_parts([{"type": "text", "data": {"text": "x"}}]) == "x"
    # reasoning fallback 到 data.text
    p4 = SimpleNamespace(type="reasoning", data={"text": "rt"})
    assert history.text_from_parts([p4], include_reasoning=True) == "[reasoning]\nrt\n[/reasoning]"
    assert history.text_from_parts([]) == ""
    assert history.text_from_parts(None) == ""


def test_history_load_basic(monkeypatch):
    captured = {}
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: None)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: None)

    def fake_list(sid, after_seq=0):
        captured["after_seq"] = after_seq
        return [SimpleNamespace(id="m1", type="user")]
    monkeypatch.setattr(history.repository, "list_messages", fake_list)
    monkeypatch.setattr(history.repository, "list_parts_for_messages", lambda ids: {"m1": ["p1"]})
    res = history.load("s1")
    assert captured["after_seq"] == 0
    assert res.messages[0].id == "m1"
    assert res.parts == {"m1": ["p1"]}
    assert res.epoch is None


def test_history_load_epoch_baseline(monkeypatch):
    epoch = SimpleNamespace(baseline_seq=5, snapshot=None)
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: epoch)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: None)
    captured = {}

    def fake_list(sid, after_seq=0):
        captured["after_seq"] = after_seq
        return []
    monkeypatch.setattr(history.repository, "list_messages", fake_list)
    history.load("s1")
    assert captured["after_seq"] == 5


def test_history_load_compaction_checkpoint(monkeypatch):
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: None)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: 10)

    def fake_list(sid, after_seq=0, limit=None):
        if after_seq == 9:
            return [SimpleNamespace(id="cp", type="compaction")]
        return [
            SimpleNamespace(id="m1", type="user"),
            SimpleNamespace(id="m2", type="assistant"),
            SimpleNamespace(id="m3", type="compaction"),  # 主列表中的 compaction 应被过滤
        ]
    monkeypatch.setattr(history.repository, "list_messages", fake_list)
    res = history.load("s1")
    assert [m.id for m in res.messages] == ["cp", "m1", "m2"]


def test_history_load_tail_start_seq(monkeypatch):
    epoch = SimpleNamespace(baseline_seq=10, snapshot={"tail_start_seq": 3})
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: epoch)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: None)
    captured = {}

    def fake_list(sid, after_seq=0):
        captured["after_seq"] = after_seq
        return []
    monkeypatch.setattr(history.repository, "list_messages", fake_list)
    history.load("s1")
    assert captured["after_seq"] == 2  # min(10, 3-1)


def test_history_load_tail_start_id(monkeypatch):
    epoch = SimpleNamespace(baseline_seq=10, snapshot={"tail_start_id": "mid"})
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: epoch)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: None)
    monkeypatch.setattr(history.repository, "seq_of_message", lambda sid, mid: 7)
    captured = {}

    def fake_list(sid, after_seq=0):
        captured["after_seq"] = after_seq
        return []
    monkeypatch.setattr(history.repository, "list_messages", fake_list)
    history.load("s1")
    assert captured["after_seq"] == 6


def test_history_load_tail_invalid_ignored(monkeypatch):
    epoch = SimpleNamespace(baseline_seq=10, snapshot={"tail_start_seq": "abc"})
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: epoch)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: None)
    captured = {}

    def fake_list(sid, after_seq=0):
        captured["after_seq"] = after_seq
        return []
    monkeypatch.setattr(history.repository, "list_messages", fake_list)
    history.load("s1")
    assert captured["after_seq"] == 10


def test_history_load_no_ids_no_parts(monkeypatch):
    epoch = SimpleNamespace(baseline_seq=100, snapshot=None)
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: epoch)
    monkeypatch.setattr(history.repository, "latest_compaction_seq", lambda sid: None)
    monkeypatch.setattr(history.repository, "list_messages", lambda sid, after_seq=0: [])

    def boom(ids):
        raise AssertionError("should not be called with empty ids")
    monkeypatch.setattr(history.repository, "list_parts_for_messages", boom)
    res = history.load("s1")
    assert res.parts == {}


def test_initialize_epoch_existing(monkeypatch):
    existing = SimpleNamespace(session_id="s1", baseline="b", baseline_seq=1, snapshot={})
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: existing)
    monkeypatch.setattr(history.repository, "upsert_epoch", lambda *a: None)
    assert history.initialize_epoch("s1", "b", {}) is existing


def test_initialize_epoch_new(monkeypatch):
    monkeypatch.setattr(history.repository, "get_epoch", lambda sid: None)
    monkeypatch.setattr(history.repository, "latest_seq", lambda sid: 42)
    upserted = {}

    def fake_upsert(sid, baseline, seq, snapshot):
        upserted.update(sid=sid, baseline=baseline, seq=seq, snapshot=snapshot)
    monkeypatch.setattr(history.repository, "upsert_epoch", fake_upsert)
    epoch = history.initialize_epoch("s1", "base", {"k": 1})
    assert epoch.baseline_seq == 42
    assert epoch.snapshot == {"k": 1}
    assert upserted == {"sid": "s1", "baseline": "base", "seq": 42, "snapshot": {"k": 1}}


def test_replace_epoch_after_compaction(monkeypatch):
    monkeypatch.setattr(history.repository, "latest_seq", lambda sid: 99)
    upserted = {}

    def fake_upsert(sid, baseline, seq, snapshot):
        upserted.update(baseline=baseline, seq=seq, snapshot=snapshot)
    monkeypatch.setattr(history.repository, "upsert_epoch", fake_upsert)
    epoch = history.replace_epoch_after_compaction("s1", "newbase", {"t": 1})
    assert epoch.baseline_seq == 99
    assert epoch.baseline == "newbase"
    assert upserted == {"baseline": "newbase", "seq": 99, "snapshot": {"t": 1}}


# ── stream_events（扩展）───────────────────────────────────────────────────

def test_agent_meta_known_and_unknown():
    assert agent_meta("rag") == (AGENT_LABELS["rag"], AGENT_AVATARS["rag"])
    assert agent_meta("mystery-agent") == ("mystery-agent", "🤖")


def test_emit_null_queue():
    emit(None, {"type": "x"})  # 不应抛异常


def test_emit_queue_error_silent():
    class BoomQueue:
        def put_nowait(self, event):
            raise RuntimeError("queue full")
    emit(BoomQueue(), {"type": "x"})


def test_unwrap_tagged():
    q = asyncio.Queue()
    c = AgentEventCollector(q)
    assert unwrap_tagged(q) is q
    tagged = TaggedEventQueue(c, "rag")
    assert unwrap_tagged(tagged) is c


async def test_collector_overflow_drops_step_keeps_terminal():
    q = asyncio.Queue()
    c = AgentEventCollector(q)
    c._MAX_EVENTS = 2
    for i in range(4):
        c.put_nowait({"type": "agent_step", "agent_id": "rag", "step": {"step_id": f"s{i}"}})
    assert c._dropped == 2
    assert any(
        e.get("step", {}).get("overflow")
        for e in c.events
    )
    c.put_nowait({"type": "agent_done", "agent_id": "rag", "content": "done"})
    assert c.events[-1]["type"] == "agent_done"  # 终态不被丢弃


async def test_agents_snapshot_lifecycle():
    q = asyncio.Queue()
    c = AgentEventCollector(q)
    c.put_nowait({"type": "agent_start", "agent_id": "rag", "agent_name": "知识库检索", "agent_avatar": "📚"})
    c.put_nowait({"type": "agent_step", "agent_id": "rag", "step": {"step_id": "t1", "name": "检索", "status": "running"}})
    c.put_nowait({"type": "agent_step", "agent_id": "rag", "step": {"step_id": "t1", "name": "检索", "status": "completed"}})
    c.put_nowait({"type": "agent_done", "agent_id": "rag", "content": "final"})
    c.put_nowait({"type": "agent_error", "agent_id": "web_search", "error": "boom"})
    c.put_nowait({"type": "mystery", "agent_id": "code"})
    snaps = c.agents_snapshot()
    by_id = {a["agent_id"]: a for a in snaps}
    assert by_id["rag"]["status"] == "completed"
    assert by_id["rag"]["content"] == "final"
    assert by_id["rag"]["agent_name"] == "知识库检索"
    assert by_id["rag"]["steps"] == [{"step_id": "t1", "name": "检索", "status": "completed"}]
    assert by_id["web_search"]["status"] == "failed"
    assert by_id["web_search"]["error"] == "boom"
    assert by_id["code"]["status"] == "running"  # 未知事件类型只建条目不改状态


async def test_agents_snapshot_agent_start_updates_meta():
    q = asyncio.Queue()
    c = AgentEventCollector(q)
    c.put_nowait({"type": "agent_step", "agent_id": "code", "step": {"step_id": "s1"}})
    c.put_nowait({"type": "agent_start", "agent_id": "code", "agent_name": "代码分析"})
    by_id = {a["agent_id"]: a for a in c.agents_snapshot()}
    assert by_id["code"]["agent_name"] == "代码分析"


async def test_fail_running():
    q = asyncio.Queue()
    c = AgentEventCollector(q)
    c.put_nowait({"type": "agent_start", "agent_id": "rag"})
    c.put_nowait({"type": "agent_done", "agent_id": "web_search", "content": "ok"})
    c.fail_running("timeout after 150s")
    by_id = {a["agent_id"]: a for a in c.agents_snapshot()}
    assert by_id["rag"]["status"] == "failed"
    assert by_id["rag"]["error"] == "timeout after 150s"
    assert by_id["web_search"]["status"] == "completed"


def test_step_event_builder():
    ev = step_event("s1", "检索", "running", detail="d", tool_name="tool_retrieve", tool_args={"q": "x"})
    assert ev["type"] == "step_start"
    assert ev["detail"] == "d"
    assert ev["tool_name"] == "tool_retrieve"
    assert ev["tool_args"] == {"q": "x"}
    ev2 = step_event("s1", "检索", "completed", duration_ms=123.456)
    assert ev2["type"] == "step_end"
    assert ev2["duration_ms"] == 123.5
    ev3 = step_event("s2", "无字段", "running")
    assert "detail" not in ev3 and "duration_ms" not in ev3 and "tool_name" not in ev3


async def test_tagged_drops_heartbeat_and_drop_types():
    q = asyncio.Queue()
    c = AgentEventCollector(q)
    tagged = TaggedEventQueue(c, "rag")
    tagged.put_nowait({"type": "tool_heartbeat", "ts": 1})
    tagged.put_nowait({"type": "tool_output", "content": "x"})
    tagged.put_nowait({"type": "unknown_future_event"})
    assert q.empty()
    assert c.agents_snapshot() == []