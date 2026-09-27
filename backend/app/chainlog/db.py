"""全链路日志数据库连接（chain_logs 子系统）。

与 `app/session/db.py` 同样的约定：
- **sqlite**：默认落在 `backend/data/chain_logs.db`，建表 DDL 由
  `app.storage.schema.derive_sqlite_ddl(CHAIN_LOG_TABLES)` 从统一 metadata 编译
  （与 Alembic 迁移同源），连接开启 WAL + busy_timeout，线程内复用。
- **mysql / postgresql**：`_get_db()` 返回 `app.storage.backends` 的连接门面，
  方言（`?`→`%s`、MySQL 反引号/ON DUPLICATE KEY）由后端层翻译。

与 session.db 的区别：链路日志是**高频追加 + 低频查询**，读多写少且写全部集中在
单个后台落盘线程，因此这里用「线程内复用连接 + 写线程独占连接」而非连接池——
日志写入永不与业务请求争抢 session.db 的连接配额。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from app.storage import backends, schema as storage_schema

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chain_logs.db"

_SCHEMA = storage_schema.derive_sqlite_ddl(storage_schema.CHAIN_LOG_TABLES)

_tls = threading.local()


def _open_sqlite() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return backends.make_sqlite_conn(
        DB_PATH,
        row_factory=sqlite3.Row,
        wal=True,
        busy_timeout=10000,
        schema_ddl=_SCHEMA,
    )


def _get_db(path: Optional[Path] = None) -> Any:
    """获取连接：非 sqlite 走统一后端门面，sqlite 走线程内复用连接。

    sqlite 连接为「创建者线程独占」语义（`check_same_thread=False` 只是为了让
    门面层统一，调用方不应跨线程共享返回值）——业务/查询走线程内缓存，落盘线程
    用 `_new_writer_conn()` 拿自己的连接。
    """
    if path is not None:
        return backends.make_sqlite_conn(
            path, row_factory=sqlite3.Row, wal=True, busy_timeout=10000,
            schema_ddl=_SCHEMA,
        )
    if not backends.is_sqlite():
        return backends.connect()
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = _open_sqlite()
        _tls.conn = conn
    return conn


def _new_writer_conn() -> Any:
    """为落盘线程新建独占连接（sqlite 专用；非 sqlite 返回门面连接）。"""
    if not backends.is_sqlite():
        return backends.connect()
    return _open_sqlite()


def init_db() -> None:
    """幂等建表（sqlite 连接时已 executescript DDL，这里再确认一次可连接）。"""
    conn = _get_db()
    if not backends.is_sqlite():
        conn.close()


def close_thread_conn() -> None:
    """关闭当前线程复用的 sqlite 连接（测试/后台线程退出时调用）。"""
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        _tls.conn = None
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
