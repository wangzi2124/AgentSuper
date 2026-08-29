# -*- coding: utf-8 -*-
"""memory.py 全量用例：持久化加载/写入、TTL 过期、namespace 隔离、标签检索、
清理、异步去抖落盘。

运行：pytest tests/test_memory.py
"""
import asyncio
import json
import os
import sys
import time as tmod

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.agent.memory as mem
from app.agent.memory import MemoryEntry, MemoryManager


@pytest.fixture
def mm(tmp_path):
    return MemoryManager(persist_path=str(tmp_path / "memory.json"))


# ── 基本 set/get/delete ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_get_delete(mm):
    await mm.set("k", "v", tags=["t"])
    assert await mm.get("k") == "v"
    assert mm.size == 1
    assert mm.active_size == 1
    await mm.delete("k")
    assert await mm.get("k") is None
    assert mm.size == 0


@pytest.mark.asyncio
async def test_get_default_and_missing(mm):
    assert await mm.get("nope", default="D") == "D"


@pytest.mark.asyncio
async def test_namespace_isolation(mm):
    await mm.set("k", "global")
    await mm.set("k", "conv1", namespace="c1")
    assert await mm.get("k") == "global"
    assert await mm.get("k", namespace="c1") == "conv1"
    assert await mm.get("k", namespace="c2") is None


@pytest.mark.asyncio
async def test_expired_get_removes(tmp_path):
    m = MemoryManager(persist_path=str(tmp_path / "m.json"))
    await m.set("k", "v", ttl=0.01)
    assert await m.get("k") == "v"
    await asyncio.sleep(0.05)
    assert await m.get("k") is None
    assert m.size == 0  # 过期条目在 get 时被清除


@pytest.mark.asyncio
async def test_active_size_counts_only_live(tmp_path):
    m = MemoryManager(persist_path=str(tmp_path / "m.json"))
    await m.set("a", 1)
    await m.set("b", 2, ttl=0.01)
    await asyncio.sleep(0.05)
    assert m.size == 2
    assert m.active_size == 1


# ── get_by_tag / to_dict / cleanup / clear ─────────────────────────────────

@pytest.mark.asyncio
async def test_get_by_tag_namespace_filter(mm):
    await mm.set("a", 1, tags=["code"], namespace="c1")
    await mm.set("b", 2, tags=["code"], namespace="c2")
    await mm.set("c", 3, tags=["other"])
    # 全局
    assert set((await mm.get_by_tag("code")).items()) == {("a", 1), ("b", 2)}
    # 指定 namespace
    assert await mm.get_by_tag("code", namespace="c1") == {"a": 1}
    assert await mm.get_by_tag("code", namespace="c2") == {"b": 2}
    # 空 namespace 精确匹配（不泄漏全局）
    assert await mm.get_by_tag("other", namespace="") == {"c": 3}
    assert await mm.get_by_tag("code", namespace="cx") == {}
    assert await mm.get_by_tag("nope") == {}


@pytest.mark.asyncio
async def test_to_dict_filters(mm):
    await mm.set("a", 1, tags=["x"], namespace="c1")
    await mm.set("b", 2, tags=["y"])
    await mm.set("c", 3, tags=["x"])
    assert set((await mm.to_dict()).items()) == {("a", 1), ("b", 2), ("c", 3)}
    assert await mm.to_dict(include_tags=["x"]) == {"a": 1, "c": 3}
    assert await mm.to_dict(namespace="c1") == {"a": 1}


@pytest.mark.asyncio
async def test_cleanup_expired(tmp_path):
    m = MemoryManager(persist_path=str(tmp_path / "m.json"))
    await m.set("live", 1)
    await m.set("dead", 2, ttl=0.01)
    await asyncio.sleep(0.05)
    await m.cleanup()
    assert m.size == 1
    assert await m.get("live") == 1


@pytest.mark.asyncio
async def test_clear_namespace(mm):
    await mm.set("a", 1, namespace="c1")
    await mm.set("b", 2, namespace="c2")
    await mm.clear_namespace("c1")
    assert mm.size == 1
    assert await mm.get("b", namespace="c2") == 2


@pytest.mark.asyncio
async def test_clear_all(mm):
    await mm.set("a", 1)
    await mm.clear()
    assert mm.size == 0


# ── 持久化 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_roundtrip(tmp_path):
    path = tmp_path / "m.json"
    m1 = MemoryManager(persist_path=str(path))
    await m1.set("k", {"nested": 1}, tags=["code"], namespace="c1")
    assert path.exists()
    m2 = MemoryManager(persist_path=str(path))
    assert await m2.get("k", namespace="c1") == {"nested": 1}


@pytest.mark.asyncio
async def test_load_skips_expired(tmp_path):
    path = tmp_path / "m.json"
    m1 = MemoryManager(persist_path=str(path))
    await m1.set("fresh", 1)
    await m1.set("stale", 2, ttl=0.01)
    await asyncio.sleep(0.05)
    m2 = MemoryManager(persist_path=str(path))
    assert await m2.get("fresh") == 1
    assert await m2.get("stale") is None


def test_load_corrupt_json(tmp_path, caplog):
    path = tmp_path / "m.json"
    path.write_text("{bad", encoding="utf-8")
    m = MemoryManager(persist_path=str(path))
    assert m.size == 0


def test_load_non_dict_entries(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"entries": [42, {"key": "k", "value": "v", "created_at": 0, "ttl": 0}]}),
                    encoding="utf-8")
    m = MemoryManager(persist_path=str(path))
    assert m.size == 0  # 非法条目被跳过；过期条目被跳过


def test_persist_no_path_no_write(tmp_path):
    m = MemoryManager(persist_path="")
    m._persist()  # 空路径直接返回，不写盘
    assert not (tmp_path / "memory.json").exists()


def test_persist_non_serializable_value(tmp_path):
    path = tmp_path / "m.json"
    m = MemoryManager(persist_path=str(path))
    m._store["obj"] = MemoryEntry(key="obj", value=object(), created_at=tmod.time())
    m._persist_sync()  # default=str 降级不抛
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data["entries"][0]["value"], str)


def test_persist_failure_warns(tmp_path, caplog):
    f = tmp_path / "blocker"
    f.write_text("x", encoding="utf-8")
    m = MemoryManager(persist_path=str(f / "sub" / "m.json"))  # parent 是文件 → mkdir 失败
    with caplog.at_level("WARNING"):
        m._persist()
    assert "persist failed" in caplog.text


@pytest.mark.asyncio
async def test_async_debounced_throttle(tmp_path, monkeypatch):
    path = tmp_path / "m.json"
    m = MemoryManager(persist_path=str(path))
    await m._persist_async_debounced()  # 首次写盘
    assert path.exists()
    calls = []

    def spy_sync():
        calls.append(1)
        m._persist_sync()
    monkeypatch.setattr(m, "_persist_sync", spy_sync)
    m._last_persist_ts = tmod.monotonic()  # 窗口内 → 跳过
    await m._persist_async_debounced()
    assert calls == []


@pytest.mark.asyncio
async def test_async_debounced_no_path():
    m = MemoryManager(persist_path="")
    assert await m._persist_async_debounced() is None


# ── MemoryEntry ────────────────────────────────────────────────────────────

def test_memory_entry_expired(tmp_path):
    e = MemoryEntry(key="k", value="v", ttl=300, created_at=tmod.time() - 301)
    assert e.expired is True
    e2 = MemoryEntry(key="k", value="v", ttl=300, created_at=tmod.time())
    assert e2.expired is False