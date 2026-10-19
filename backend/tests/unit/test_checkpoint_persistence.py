"""L1 单元测试：图检查点持久化（graph/checkpoint.py，AsyncSqliteSaver 默认）。

覆盖：默认 SQLite 跨实例存活、configured 直返、CHECKPOINT_PERSIST=disabled、
单例复用。conftest 已把 CHECKPOINT_DB_PATH 指到临时目录（默认实例隔离），
本文件用 monkeypatch 指向各自的 tmp_path 以验证路径覆盖语义。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from graph.checkpoint import (
    aclose_checkpointer,
    checkpoint_db_path,
    get_checkpointer,
)


def _ckpt(cid: str = "c1") -> dict:
    return {
        "id": cid,
        "ts": "2026-10-19T00:00:00+00:00",
        "channel_values": {"messages": ["hi"]},
        "channel_versions": {"messages": 1},
        "versions_seen": {},
        "updated_channels": ["messages"],
    }


async def test_sqlite_checkpointer_persists_across_instances(tmp_path, monkeypatch):
    db = tmp_path / "ckpt.db"
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(db))
    monkeypatch.delenv("CHECKPOINT_PERSIST", raising=False)

    await aclose_checkpointer()  # 清掉套件内可能残留的单例
    try:
        saver = await get_checkpointer()
        assert checkpoint_db_path() == db

        cfg = {"configurable": {"thread_id": "t_persist", "checkpoint_ns": ""}}
        new_cfg = await saver.aput(cfg, _ckpt("c1"), {"source": "test"}, {})
        assert await saver.aget_tuple(new_cfg) is not None

        # 模拟重启：关闭 → 重新打开（同一文件）→ 数据仍在
        await aclose_checkpointer()
        saver2 = await get_checkpointer()
        got = await saver2.aget_tuple(new_cfg)
        assert got is not None
        assert got.checkpoint["id"] == "c1"
        assert db.exists()
    finally:
        await aclose_checkpointer()


async def test_configured_passthrough():
    sentinel = MemorySaver()
    assert await get_checkpointer(sentinel) is sentinel


async def test_persist_disabled_returns_memory(monkeypatch):
    monkeypatch.setenv("CHECKPOINT_PERSIST", "false")
    saver = await get_checkpointer()
    assert isinstance(saver, MemorySaver)


async def test_singleton_reuses_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.delenv("CHECKPOINT_PERSIST", raising=False)
    await aclose_checkpointer()
    try:
        a = await get_checkpointer()
        b = await get_checkpointer()
        assert a is b
    finally:
        await aclose_checkpointer()
