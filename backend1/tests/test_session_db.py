# -*- coding: utf-8 -*-
"""session/db.py 连接池用例：_ConnectionPool acquire/release/超限、_PooledConnection
代理 close 归还/再 close 真关、init_db、自定义路径 _get_db。

运行：pytest tests/test_session_db.py
"""
import os
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.session.db as sdb
from app.session.db import _ConnectionPool, _PooledConnection


def test_pool_acquire_release_reuse():
    pool = _ConnectionPool(max_size=2)
    c1 = pool.acquire()
    c2 = pool.acquire()
    # 两个连接都进池回收
    assert pool.release(c1._conn) is True
    assert pool.release(c2._conn) is True
    c3 = pool.acquire()
    # 复用 idle 连接，不新建（_created 保持 2）
    assert pool._created == 2
    assert c3._conn in (c1._conn, c2._conn)


def test_pool_exceeds_max_non_pooled():
    pool = _ConnectionPool(max_size=1)
    c1 = pool.acquire()
    c2 = pool.acquire()  # 超限 → 非池化（pool=None）
    assert c1._pool is pool
    assert c2._pool is None
    c2.close()  # 非池化 close → 真正关闭（不回收）
    assert len(pool._idle) == 0
    assert pool.release(c1._conn) is True

    c3 = pool.acquire()
    c4 = pool.acquire()  # 池满超限 → 非池化
    assert c3._conn is c1._conn  # 复用 idle 连接
    assert c4._pool is None
    # release 只看 idle 容量：先放 c3 填满，c4 再放 → False（由调用方真正关闭）
    assert pool.release(c3._conn) is True
    assert pool.release(c4._conn) is False


def test_pooled_connection_proxy():
    pool = _ConnectionPool(max_size=1)
    conn = pool.acquire()
    # 属性委托
    conn.execute("SELECT 1")
    conn.close()  # 归还池
    assert conn._pool is None  # 防止二次归还
    conn.close()  # 真正关闭（幂等）


def test_pooled_connection_context_manager(tmp_path):
    pool = _ConnectionPool(max_size=1)
    with pool.acquire() as conn:
        conn.execute("SELECT 1")
    assert len(pool._idle) == 1


def test_get_db_custom_path(tmp_path):
    path = tmp_path / "custom" / "db.sqlite"
    conn = sdb._get_db(path)
    try:
        assert conn is not None
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        # schema 已建
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_init_db(monkeypatch, tmp_path):
    path = tmp_path / "init.db"
    orig = sdb._get_db

    def fake_get_db():
        return orig(path)
    monkeypatch.setattr(sdb, "_get_db", fake_get_db)
    sdb.init_db()  # 幂等不抛