# -*- coding: utf-8 -*-
"""统一存储后端（app/storage）与 Alembic 迁移链用例。

覆盖：
- metadata 派生 sqlite DDL（表/索引齐备、幂等）
- 方言翻译：? → %s、ON CONFLICT → ON DUPLICATE KEY UPDATE / VALUES(x)、BEGIN IMMEDIATE → None
- Alembic upgrade head 在临时库建出四个子系统的全部表
- 连接门面 Row/Cursor 的 sqlite3 兼容语义（row["col"] / row[0] / dict(row) / 迭代解包 / IN 参数）
- database_url() 组件拼装（mysql / postgresql）
"""
import os
import sqlite3
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from sqlalchemy import create_engine

import app.storage.schema as storage_schema
from app.storage import backends
from app.config import settings


@pytest.fixture(autouse=True)
def _restore_settings():
    saved = (settings.db_type, settings.db_url, settings.db_host,
             settings.db_port, settings.db_username, settings.db_password, settings.db_name)
    yield
    (settings.db_type, settings.db_url, settings.db_host,
     settings.db_port, settings.db_username, settings.db_password, settings.db_name) = saved


def test_derive_sqlite_ddl_tables_and_indexes():
    ddl = storage_schema.derive_sqlite_ddl(storage_schema.SESSION_TABLES)
    conn = sqlite3.connect(":memory:")
    conn.executescript(ddl)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert {"projects", "workspaces", "sessions", "session_messages",
            "message_parts", "session_context_epoch", "session_inputs",
            "session_tasks"} <= tables
    assert {"idx_sessions_user", "idx_sessions_project", "idx_sessions_parent",
            "idx_sessions_updated", "idx_messages_session_time", "idx_parts_message",
            "idx_tasks_session"} <= indexes


def test_derive_sqlite_ddl_idempotent():
    ddl = storage_schema.derive_sqlite_ddl(storage_schema.CATALOG_TABLES)
    conn = sqlite3.connect(":memory:")
    conn.executescript(ddl)
    conn.executescript(ddl)  # 全 IF NOT EXISTS，重复执行不抛
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='providers'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_mysql_translation():
    sql = (
        "INSERT INTO tasks (id, status) VALUES (?, ?) "
        "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
        "total_tokens=excluded.total_tokens"
    )
    out = backends.translate_sql_for_test(sql)
    assert out == (
        "INSERT INTO tasks (id, status) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE status=VALUES(status), "
        "total_tokens=VALUES(total_tokens)"
    )


def test_begin_immediate_skipped():
    assert backends.translate_sql_for_test("BEGIN IMMEDIATE") is None
    assert backends.translate_sql_for_test("  begin immediate; ") is None


def test_pg_keeps_on_conflict():
    from app.storage.backends import _translate_sql
    sql = "INSERT INTO tasks (id) VALUES (?) ON CONFLICT(id) DO UPDATE SET status=excluded.status"
    out = _translate_sql(sql, "postgresql")
    assert "ON CONFLICT(id) DO UPDATE" in out
    assert "excluded.status" in out
    assert "?" not in out


@pytest.mark.parametrize("kind,expected_start", [
    ("mysql", "mysql+pymysql://root:secret@127.0.0.1:3306/agentsuper?charset=utf8mb4"),
    ("postgresql", "postgresql+psycopg2://root:secret@127.0.0.1:5432/agentsuper"),
])
def test_database_url_composed(kind, expected_start):
    settings.db_type = kind
    settings.db_url = None
    settings.db_host = "127.0.0.1"
    settings.db_port = 0
    settings.db_username = "root"
    settings.db_password = "secret"
    settings.db_name = "agentsuper"
    assert backends.database_url() == expected_start


def test_database_url_prefers_db_url():
    settings.db_url = "mysql+pymysql://override:1@db:3307/x"
    assert backends.database_url() == "mysql+pymysql://override:1@db:3307/x"


def test_alembic_upgrade_head_creates_all_subsystems(tmp_path):
    """Alembic 迁移链在空库建出四个子系统的全部表（跨后端共享同一数据库）。"""
    import alembic.config
    import alembic.command

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = tmp_path / "mig.db"
    cfg = alembic.config.Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    alembic.command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert tables == {
        "alembic_version",
        "projects", "workspaces", "sessions", "session_messages", "message_parts",
        "session_context_epoch", "session_inputs", "session_tasks",
        "catalog_settings", "providers", "catalog_entries",
        "chapters",
        "tasks",
    }


def test_alembic_run_migrations_runner(tmp_path):
    """run_migrations() 在 db_type=postgresql / db_url 指向临时 sqlite 时执行 upgrade head。"""
    from app.storage.migrations import run_migrations

    db = tmp_path / "runner.db"
    settings.db_type = "postgresql"
    settings.db_url = f"sqlite:///{db}"
    assert run_migrations() is True
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "sessions" in tables and "tasks" in tables


def test_run_migrations_noop_for_sqlite():
    from app.storage.migrations import run_migrations

    settings.db_type = "sqlite"
    settings.db_url = None
    assert run_migrations() is False


def test_facade_row_cursor_compat(tmp_path):
    """连接门面对 repository/catalog/chapter/task 调用的兼容面。"""
    db = tmp_path / "f.db"
    engine = create_engine(f"sqlite:///{db}")
    storage_schema.metadata.create_all(engine)

    conn = backends.Connection(engine.connect())
    conn.execute("DELETE FROM projects")
    conn.execute(
        "INSERT INTO projects (id, name, root, vcs, time_created, time_updated)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("p1", "proj", "/x", "", 1, 2),
    )
    conn.commit()

    row = conn.execute("SELECT id, name FROM projects WHERE id = ?", ("p1",)).fetchone()
    assert row["id"] == "p1"
    assert row[1] == "proj"
    assert dict(row) == {"id": "p1", "name": "proj"}

    seen = []
    for sid, name in conn.execute("SELECT id AS sid, name FROM projects"):
        seen.append((sid, name))
    assert seen == [("p1", "proj")]

    cur = conn.execute("SELECT id FROM projects WHERE id IN (?, ?)", ["p1", "p2"])
    assert [r["id"] for r in cur.fetchall()] == ["p1"]
    assert conn.execute("DELETE FROM projects WHERE id = ?", ("p1",)).rowcount == 1
    conn.commit()
    conn.close()


def test_active_kind_defaults_sqlite():
    # .env 可能已把 DB_TYPE 配成 mysql/pg，这里显式清空测"未配置默认"分支
    settings.db_type = None
    settings.db_url = None
    assert backends.active_kind() == "sqlite"
    assert backends.is_sqlite() is True