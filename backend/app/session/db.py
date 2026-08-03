"""Session 数据库连接与建表。

独立于旧的 conversations.db（只读保留），新建 session.db 承载归一化的
session / session_messages / message_parts / context_epoch / session_inputs。

对应 opencode 设计：
- sessions.project_id / workspace_id / parent_id 三级隔离（sql.ts）
- session_messages append-only 事件日志 + seq（sql.ts）
- session_context_epoch per-session 上下文快照（context-epoch.ts）
"""

import sqlite3
from pathlib import Path
from typing import Optional

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


def _get_db(path: Optional[Path] = None) -> sqlite3.Connection:
    """获取连接并初始化表结构（默认使用 session.db）。"""
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def init_db() -> None:
    """幂等建表，供应用启动时调用。"""
    conn = _get_db()
    try:
        conn.close()
    except Exception:
        pass
