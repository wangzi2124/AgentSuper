"""Session 数据库连接与建表。

session.db 承载归一化的 session / session_messages / message_parts /
context_epoch / session_inputs（旧的 conversations.db 已移除，数据全部在 session.db）。

对应 opencode 设计：
- sessions.project_id / workspace_id / parent_id 三级隔离（sql.ts）
- session_messages append-only 事件日志 + seq（sql.ts）
- session_context_epoch per-session 上下文快照（context-epoch.ts）

后端支持：
- **sqlite**：沿用本模块内的 _ConnectionPool（原生 sqlite3，零回归），
  _SCHEMA 由 `app.storage.schema.derive_sqlite_ddl` 从单一 metadata 编译，
  与 Alembic 初始迁移同源。
- **mysql / postgresql**：_get_db() 返回 `app.storage.backends` 的连接门面
  （SQLAlchemy 引擎，参数/事务方言由门面翻译），本模块的 Pool 不参与。
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.config import settings
from app.storage import backends, schema as storage_schema

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "session.db"

# 由 metadata 表声明编译的 sqlite DDL（建表 + 建索引，全部 IF NOT EXISTS）
_SESSION_TABLES = storage_schema.SESSION_TABLES
_SCHEMA = storage_schema.derive_sqlite_ddl(_SESSION_TABLES)


class _PooledConnection:
    """sqlite3.Connection 的池化代理：close() 时归还连接池而非真正关闭。

    说明：sqlite3.Connection 是 C 类型，不允许挂载任意属性（'no __dict__'），
    因此用轻量代理承接 close() 语义；其余属性/方法经 __getattr__/__setattr__
    全量委托给底层连接，对调用方透明（repository.py 无需改动）。
    """

    def __init__(self, conn: sqlite3.Connection, pool: Optional["_ConnectionPool"]):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self) -> None:
        conn = object.__getattribute__(self, "_conn")
        pool = object.__getattribute__(self, "_pool")
        if pool is not None and pool.release(conn):
            # 已归还池：同一代理再次 close() 将走真正关闭（防双重归还）
            object.__setattr__(self, "_pool", None)
            return
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等补列：老版本 session.db 缺列时 ALTER TABLE 增加（CREATE TABLE 已含新列则跳过）。"""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()


class _ConnectionPool:
    """session.db 连接池（WAL 模式，连接复用，避免每请求开关）。

    - acquire() 有空闲连接则复用；否则新建（上限 max_size 个池化连接）。
    - 达到上限时临时新建非池化连接，用完即真正关闭——不阻塞、不泄漏。
    - release() 在池未满时回收；池满返回 False，由调用方真正关闭。
    """

    def __init__(self, max_size: int):
        self._max = max_size
        self._lock = threading.Lock()
        self._idle: list[sqlite3.Connection] = []
        self._created = 0

    def acquire(self) -> sqlite3.Connection:
        with self._lock:
            if self._idle:
                return _PooledConnection(self._idle.pop(), self)
            if self._created < self._max:
                self._created += 1
                pooled = True
            else:
                pooled = False
        return _PooledConnection(self._open(), self if pooled else None)

    def release(self, conn: sqlite3.Connection) -> bool:
        with self._lock:
            if len(self._idle) >= self._max:
                return False
            self._idle.append(conn)
            return True

    def _open(self) -> sqlite3.Connection:
        db_path = DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = backends.make_sqlite_conn(
            db_path,
            row_factory=sqlite3.Row,
            foreign_keys=True,
            wal=True,
            busy_timeout=10000,
            schema_ddl=_SCHEMA,
        )
        _ensure_column(conn, "sessions", "tokens_reasoning", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        return conn


# 池大小与并发上限联动（+2 余量），避免多 Agent 同时执行时连接耗尽重建
_pool = _ConnectionPool(max(6, settings.max_concurrent_agents + 2))


def _get_db(path: Optional[Path] = None):
    """获取连接并初始化表结构（默认使用 session.db，走连接池；非 sqlite 走统一后端）。"""
    if path is not None:
        # 自定义路径强制 sqlite 文件（rare：测试/迁移，独立连接；与后端方言无关）
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = backends.make_sqlite_conn(
            path,
            row_factory=sqlite3.Row,
            foreign_keys=True,
            wal=True,
            busy_timeout=10000,
            schema_ddl=_SCHEMA,
        )
        _ensure_column(conn, "sessions", "tokens_reasoning", "INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        return conn
    if not backends.is_sqlite():
        # 非 sqlite：返回统一后端门面（repository 方法会在 try/finally 中 close）
        return backends.connect()
    return _pool.acquire()


def init_db() -> None:
    """幂等建表，供应用启动时调用。"""
    conn = _get_db()
    try:
        conn.close()
    except Exception:
        pass
