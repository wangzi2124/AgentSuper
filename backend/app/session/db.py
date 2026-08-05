"""Session 数据库连接与建表。

独立于旧的 conversations.db（只读保留），新建 session.db 承载归一化的
session / session_messages / message_parts / context_epoch / session_inputs。

对应 opencode 设计：
- sessions.project_id / workspace_id / parent_id 三级隔离（sql.ts）
- session_messages append-only 事件日志 + seq（sql.ts）
- session_context_epoch per-session 上下文快照（context-epoch.ts）
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from app.config import settings

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "session.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL DEFAULT '',
  root       TEXT NOT NULL,
  vcs        TEXT NOT NULL DEFAULT '',
  time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name       TEXT NOT NULL DEFAULT '',
  time_created INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  slug       TEXT NOT NULL,
  version    TEXT NOT NULL DEFAULT '1',
  user_id    TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
  parent_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
  directory  TEXT NOT NULL,
  path       TEXT NOT NULL DEFAULT '',
  title      TEXT NOT NULL,
  agent      TEXT,
  model      TEXT,
  kind       TEXT NOT NULL DEFAULT 'chat',
  status     TEXT NOT NULL DEFAULT 'idle',
  cost       REAL NOT NULL DEFAULT 0,
  tokens_input INTEGER NOT NULL DEFAULT 0,
  tokens_output INTEGER NOT NULL DEFAULT 0,
  tokens_cache_read INTEGER NOT NULL DEFAULT 0,
  tokens_cache_write INTEGER NOT NULL DEFAULT 0,
  time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL,
  time_compacted INTEGER,
  time_archived INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(time_updated);

CREATE TABLE IF NOT EXISTS session_messages (
  seq         INTEGER NOT NULL,
  id          TEXT NOT NULL,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,
  data        TEXT NOT NULL,
  time_created INTEGER NOT NULL,
  PRIMARY KEY (session_id, seq),
  UNIQUE (session_id, id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_time ON session_messages(session_id, time_created);

CREATE TABLE IF NOT EXISTS message_parts (
  id          TEXT NOT NULL,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  message_id  TEXT NOT NULL,
  type        TEXT NOT NULL,
  data        TEXT NOT NULL,
  time_created INTEGER NOT NULL,
  PRIMARY KEY (session_id, id)
);
CREATE INDEX IF NOT EXISTS idx_parts_message ON message_parts(message_id);

CREATE TABLE IF NOT EXISTS session_context_epoch (
  session_id   TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
  baseline     TEXT NOT NULL,
  baseline_seq INTEGER NOT NULL,
  snapshot     TEXT NOT NULL,
  time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_inputs (
  id           TEXT NOT NULL,
  session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  prompt       TEXT NOT NULL,
  delivery     TEXT NOT NULL DEFAULT 'steer',
  admitted_seq INTEGER NOT NULL,
  promoted_seq INTEGER,
  time_created INTEGER NOT NULL,
  PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS session_tasks (
  id           TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  parent_task_id TEXT REFERENCES session_tasks(id) ON DELETE CASCADE,
  status       TEXT NOT NULL DEFAULT 'running',
  step         INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  tool_calls_count INTEGER NOT NULL DEFAULT 0,
  time_created INTEGER NOT NULL,
  time_updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON session_tasks(session_id);
"""


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
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn


# 池大小与并发上限联动（+2 余量），避免多 Agent 同时执行时连接耗尽重建
_pool = _ConnectionPool(max(6, settings.max_concurrent_agents + 2))


def _get_db(path: Optional[Path] = None) -> sqlite3.Connection:
    """获取连接并初始化表结构（默认使用 session.db，走连接池）。"""
    if path is not None:
        # 自定义路径不走池（rare：测试/迁移），保持独立连接
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn
    return _pool.acquire()


def init_db() -> None:
    """幂等建表，供应用启动时调用。"""
    conn = _get_db()
    try:
        conn.close()
    except Exception:
        pass
