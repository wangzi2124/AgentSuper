"""SQLite / MySQL / PostgreSQL 统一后端层。

设计要点：
- `active_kind()` 从 settings 取 db_type；sqlite 走各子系统原生 sqlite3 路径（零回归），
  mysql/postgresql 走 SQLAlchemy 引擎 + 本模块的连接门面。
- 非 sqlite 连接门面（Connection/Cursor/Row）镜像 sqlite3 的调用面：
  `execute(sql, params)` / `fetchone` / `fetchall` / 迭代 / `row["col"]` / `row[0]` /
  `dict(row)` / `rowcount` / `commit` / `close` / `with conn:` —— 上层的 repository /
  catalog / chapter / task 代码无需感知后端差异。
- 方言翻译（仅非 sqlite）：
  - 参数占位符 `?` → `%s`（pymysql 与 psycopg2 均接受 %s）
  - `ON CONFLICT(...) DO UPDATE SET a=excluded.a` →（mysql）`ON DUPLICATE KEY UPDATE a=VALUES(a)`；
    PostgreSQL 与 sqlite 语法相同，原样保留
  - `BEGIN IMMEDIATE` → 空操作（依赖 SQLAlchemy 隐式事务 + 应用层 write_lock 串行化）
- schema：非 sqlite 由 Alembic 迁移链创建（`app/storage/migrations.run_migrations()`）；
  `init_schema()` 作为幂等兜底（等价于初始迁移，同源于 metadata）。
"""

from __future__ import annotations

import re
import threading
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection as SAConnection
from sqlalchemy.engine import Engine

from app.config import settings

_engine: Optional[Engine] = None
_engine_lock = threading.RLock()
_schema_ready = False
_thread_state = threading.local()

_MYSQL_PARAM_RE = re.compile(r"\?")
_MYSQL_IDENT_RE = re.compile(r"(?<![\w`])(key|value)(?![\w`])", re.IGNORECASE)
_MYSQL_CONFLICT_RE = re.compile(
    r"ON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\s+", re.IGNORECASE
)
_MYSQL_EXCLUDED_RE = re.compile(
    r"\b([A-Za-z_][\w]*)\s*=\s*excluded\.\1\b", re.IGNORECASE
)
_PG_BEGIN_RE = re.compile(r"^\s*BEGIN\s+IMMEDIATE\s*;?\s*$", re.IGNORECASE)


def active_kind() -> str:
    """当前持久化后端类型：sqlite | mysql | postgresql。"""
    return (settings.db_type or "sqlite").strip().lower()


def is_sqlite() -> bool:
    return active_kind() == "sqlite"


def database_url() -> str:
    """构造 SQLAlchemy 连接串。DB_URL 优先，否则按组件字段拼装。"""
    if settings.db_url:
        return settings.db_url
    kind = active_kind()
    if kind == "mysql":
        port = settings.db_port or 3306
        return (
            f"mysql+pymysql://{settings.db_username}:{settings.db_password}"
            f"@{settings.db_host}:{port}/{settings.db_name}?charset=utf8mb4"
        )
    if kind == "postgresql":
        port = settings.db_port or 5432
        return (
            f"postgresql+psycopg2://{settings.db_username}:{settings.db_password}"
            f"@{settings.db_host}:{port}/{settings.db_name}"
        )
    raise ValueError(
        f"Unsupported DB_TYPE: {settings.db_type!r} (expected sqlite|mysql|postgresql)"
    )


def get_engine() -> Engine:
    """取（缓存）非 sqlite 引擎。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = create_engine(
                    database_url(),
                    pool_pre_ping=True,
                    pool_recycle=1800,
                )
    return _engine


def init_schema() -> None:
    """非 sqlite：幂等建表兜底（等价于初始 Alembic 迁移，均源于 storage.schema 的 metadata）。

    sqlite 后端由各子系统连接时自建表，这里直接跳过。
    """
    global _schema_ready
    if is_sqlite():
        return
    if _schema_ready:
        return
    with _engine_lock:
        if _schema_ready:
            return
        from app.storage import schema as storage_schema

        storage_schema.metadata.create_all(get_engine())
        _schema_ready = True


def _translate_sql(sql: str, kind: str) -> Optional[str]:
    """方言翻译；返回 None 表示该语句应被跳过（BEGIN IMMEDIATE）。"""
    if kind == "sqlite":
        return sql
    if _PG_BEGIN_RE.match(sql):
        return None
    out = _MYSQL_PARAM_RE.sub("%s", sql)
    if kind == "mysql":
        # MySQL 保留字 `key`/`value`（catalog_settings 列名）在 DDL 中会被
        # SQLAlchemy 方言自动加反引号，手写 DML 需同样转义；PG/sqlite 非保留字。
        out = _MYSQL_IDENT_RE.sub(r"`\1`", out)
        out = _MYSQL_CONFLICT_RE.sub("ON DUPLICATE KEY UPDATE ", out)
        out = _MYSQL_EXCLUDED_RE.sub(
            lambda m: f"{m.group(1)}=VALUES({m.group(1)})", out
        )
    return out


class Row:
    """兼容 sqlite3.Row 的行对象（支持 dict(row)、row["col"]、row[0]、迭代/解包）。"""

    __slots__ = ("_values", "_keys", "_idx")

    def __init__(self, keys: list[str], values: tuple):
        self._keys = keys
        self._idx = {k: i for i, k in enumerate(keys)}
        self._values = tuple(values)

    def keys(self) -> list[str]:
        return self._keys

    def __getitem__(self, key: Any):
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, str):
            return self._values[self._idx[key]]
        raise TypeError(f"invalid row key: {key!r}")

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class Cursor:
    """兼容 sqlite3.Cursor 的最小子集。"""

    def __init__(self, result, conn: "Connection"):
        self._result = result
        self._conn = conn

    @property
    def rowcount(self) -> int:
        return self._result.rowcount

    def fetchone(self) -> Optional[Row]:
        row = self._result.fetchone()
        if row is None:
            return None
        return Row(list(self._result.keys()), tuple(row))

    def fetchall(self) -> list[Row]:
        keys = list(self._result.keys())
        return [Row(keys, tuple(r)) for r in self._result.fetchall()]

    def __iter__(self):
        keys = list(self._result.keys())
        return (Row(keys, tuple(r)) for r in self._result)


class Connection:
    """非 sqlite 连接门面：把 SQLAlchemy Connection 适配成 sqlite3 风格调用面。"""

    def __init__(self, sa_conn: SAConnection):
        self._conn = sa_conn
        dialect = self._conn.dialect.name
        # 方言优先：测试/迁移可能把非 sqlite 门面套在临时 sqlite 引擎上，
        # 此时以引擎实际方言为准，而不是全局 settings.db_type。
        self._kind = dialect if dialect in ("sqlite", "mysql", "postgresql") else active_kind()

    def execute(self, sql: str, params: Any = None) -> Cursor:
        translated = _translate_sql(sql, self._kind)
        if translated is None:
            return Cursor(_EmptyResult(), self)
        if params is None:
            params = ()
        elif not isinstance(params, (tuple, list)):
            params = (params,)
        result = self._conn.exec_driver_sql(translated, tuple(params))
        return Cursor(result, self)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception:
                self._conn.rollback()
        else:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self._conn.close()
        return False


class _EmptyResult:
    """BEGIN IMMEDIATE 等被跳过语句的占位结果。"""

    @property
    def rowcount(self) -> int:
        return -1

    def keys(self) -> list:
        return []

    def fetchone(self):
        return None

    def fetchall(self) -> list:
        return []

    def __iter__(self):
        return iter(())


def connect() -> Connection:
    """打开一条非 sqlite 连接（惰性建表兜底，线程安全）。"""
    init_schema()
    return Connection(get_engine().connect())


def thread_local_connect() -> Connection:
    """每线程复用的非 sqlite 连接（替代 sqlite 的线程级连接缓存习惯）。

    上层调用方可能不 close；用 thread-local 持有，复用同一连接避免反复建连/泄漏。
    """
    conn = getattr(_thread_state, "conn", None)
    if conn is None:
        conn = connect()
        _thread_state.conn = conn
    return conn


def make_sqlite_conn(
    path, *,
    row_factory=None,
    foreign_keys: bool = False,
    wal: bool = False,
    busy_timeout: Optional[int] = None,
    schema_ddl: str = "",
) -> Any:
    """按统一参数开 sqlite 连接（各行系统原来各写一份的 PRAGMA + executescript 收敛于此）。"""
    import sqlite3

    conn = sqlite3.connect(str(path), check_same_thread=False)
    if row_factory is not None:
        conn.row_factory = row_factory
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")
    if wal:
        conn.execute("PRAGMA journal_mode = WAL")
    if busy_timeout:
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout)}")
    if schema_ddl:
        conn.executescript(schema_ddl)
    return conn


def translate_sql_for_test(sql: str) -> Optional[str]:
    """测试用：暴露方言翻译结果。"""
    return _translate_sql(sql, "mysql")