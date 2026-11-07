"""Web Chat v2 持久层迁移（Phase 1）。

sqlite 的连接时迁移；mysql/postgresql 由 Alembic 0004 执行。
迁移只补列/建表/回填 ordinal，不改旧业务语义。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

V2_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("sessions", "outcome", "VARCHAR(16)"),
    ("sessions", "metadata", "TEXT NOT NULL DEFAULT '{}'"),
    ("sessions", "revert_state", "TEXT"),
    ("sessions", "time_busy", "INTEGER"),
    ("sessions", "time_idle", "INTEGER"),
    ("sessions", "time_viewed", "INTEGER"),
    ("session_messages", "time_completed", "INTEGER"),
    ("message_parts", "ordinal", "INTEGER NOT NULL DEFAULT 0"),
    ("message_parts", "time_updated", "INTEGER"),
    ("session_inputs", "type", "VARCHAR(16) NOT NULL DEFAULT 'user'"),
    ("session_inputs", "status", "VARCHAR(16) NOT NULL DEFAULT 'pending'"),
    ("session_inputs", "time_delivered", "INTEGER"),
)

MIGRATION_ID = "v2_persistence"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_v2_columns(conn: sqlite3.Connection) -> list[str]:
    added: list[str] = []
    for table, column, ddl in V2_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.append(f"{table}.{column}")
    conn.commit()
    return added


def backfill_part_ordinals(conn: sqlite3.Connection) -> int:
    """按消息内原有顺序重排 ordinal（time_created, id），幂等。"""
    rows = conn.execute(
        "SELECT session_id, message_id, id FROM message_parts"
        " ORDER BY session_id, message_id, time_created, id"
    ).fetchall()
    current_key: tuple[str, str] | None = None
    ordinal = 0
    changed = 0
    for session_id, message_id, part_id in rows:
        key = (str(session_id), str(message_id))
        if key != current_key:
            current_key = key
            ordinal = 0
        cur = conn.execute(
            "UPDATE message_parts SET ordinal = ? WHERE session_id = ? AND id = ?"
            " AND COALESCE(ordinal, -1) != ?",
            (ordinal, session_id, part_id, ordinal),
        )
        changed += max(0, cur.rowcount)
        ordinal += 1
    conn.commit()
    return changed


def backfill_input_status(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE session_inputs SET status = 'delivered',"
        " time_delivered = COALESCE(time_delivered, promoted_seq, time_created)"
        " WHERE promoted_seq IS NOT NULL AND status = 'pending'"
    )
    conn.commit()
    return max(0, cur.rowcount)


def ensure_v2_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_parts_message_ordinal"
        " ON message_parts (session_id, message_id, ordinal)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parts_message_ordinal"
        " ON message_parts (message_id, ordinal)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inputs_session_status"
        " ON session_inputs (session_id, status)"
    )
    conn.commit()


def validate_v2(conn: sqlite3.Connection) -> dict[str, Any]:
    duplicate_ordinals = conn.execute(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT session_id, message_id, ordinal FROM message_parts"
        "  GROUP BY session_id, message_id, ordinal HAVING COUNT(*) > 1"
        ")"
    ).fetchone()[0]
    missing_message = conn.execute(
        "SELECT COUNT(*) AS n FROM message_parts p"
        " LEFT JOIN session_messages m"
        "   ON m.session_id = p.session_id AND m.id = p.message_id"
        " WHERE m.id IS NULL"
    ).fetchone()[0]
    invalid_input_status = conn.execute(
        "SELECT COUNT(*) AS n FROM session_inputs"
        " WHERE status NOT IN ('pending', 'delivered', 'cancelled')"
    ).fetchone()[0]
    invalid_input_delivery = conn.execute(
        "SELECT COUNT(*) AS n FROM session_inputs"
        " WHERE delivery NOT IN ('steer', 'queue')"
    ).fetchone()[0]
    return {
        "duplicate_part_ordinals": int(duplicate_ordinals),
        "parts_without_message": int(missing_message),
        "invalid_input_status": int(invalid_input_status),
        "invalid_input_delivery": int(invalid_input_delivery),
    }


def migrate_v2(conn: sqlite3.Connection, *, force: bool = False) -> dict[str, Any]:
    """执行一次 v2 持久层迁移；已执行时直接返回 applied=False。

    迁移标记写在 schema_migrations，避免每次连接重复回填。
    """
    row = conn.execute(
        "SELECT id FROM schema_migrations WHERE id = ?", (MIGRATION_ID,)
    ).fetchone()
    if row is not None and not force:
        return {"applied": False, "validation": validate_v2(conn)}

    added = ensure_v2_columns(conn)
    ordinals = backfill_part_ordinals(conn)
    delivered = backfill_input_status(conn)
    ensure_v2_indexes(conn)
    conn.execute(
        "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)"
        " ON CONFLICT(id) DO UPDATE SET applied_at = excluded.applied_at",
        (MIGRATION_ID, int(time.time() * 1000)),
    )
    conn.commit()
    return {
        "applied": True,
        "added_columns": added,
        "part_ordinals_changed": ordinals,
        "inputs_marked_delivered": delivered,
        "validation": validate_v2(conn),
    }
