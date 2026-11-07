from __future__ import annotations

import sqlite3

import pytest

from app.session import repository
from app.session.db import _get_db
from app.session.migrations import MIGRATION_ID, migrate_v2


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "session.db"

    def _open():
        return _get_db(path)

    monkeypatch.setattr(repository, "_get_db", _open)
    return path


def _session(store) -> repository.SessionInfo:
    project = repository.resolve_project(str(store.parent))
    return repository.create_session(
        "u1", project.id, str(store.parent), title="v2 persistence"
    )


def test_v2_schema_has_columns_tables_and_marker(store) -> None:
    _get_db(store).close()
    conn = sqlite3.connect(store)
    try:
        session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        part_cols = {row[1] for row in conn.execute("PRAGMA table_info(message_parts)")}
        input_cols = {row[1] for row in conn.execute("PRAGMA table_info(session_inputs)")}
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        marker = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = ?", (MIGRATION_ID,)
        ).fetchone()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    finally:
        conn.close()

    assert {"outcome", "metadata", "revert_state", "time_busy", "time_idle", "time_viewed"} <= session_cols
    assert {"ordinal", "time_updated"} <= part_cols
    assert {"type", "status", "time_delivered"} <= input_cols
    assert {"session_permissions", "session_permission_saved", "session_forms"} <= tables
    assert marker is not None
    assert "uq_parts_message_ordinal" in indexes


def test_parts_use_monotonic_ordinal_and_upsert(store) -> None:
    sess = _session(store)
    msg = repository.upsert_message(sess.id, "msg_1", "assistant", {"content": []})

    first = repository.append_part(sess.id, msg.id, "text", {"text": "a"})
    second = repository.append_part(sess.id, msg.id, "tool", {"state": "running"})
    assert (first.ordinal, second.ordinal) == (0, 1)
    assert [p.ordinal for p in repository.list_parts(msg.id)] == [0, 1]

    updated = repository.upsert_part(
        sess.id, msg.id, first.id, "text", {"text": "a2"}, ordinal=99
    )
    assert updated.ordinal == 0
    assert repository.list_parts(msg.id)[0].data["text"] == "a2"


def test_inbox_delivery_and_cancel(store) -> None:
    sess = _session(store)
    first = repository.admit_input(
        sess.id, {"message": "first"}, delivery="queue", input_id="in_1"
    )
    second = repository.admit_input(
        sess.id, {"message": "second"}, delivery="steer", input_id="in_2"
    )
    assert first == "in_1"

    pending = repository.list_inbox(sess.id)
    assert [item["id"] for item in pending] == [second, first]
    assert [item["status"] for item in pending] == ["pending", "pending"]

    delivered = repository.promote_next(sess.id)
    assert delivered is not None
    assert delivered["id"] == second
    assert delivered["status"] == "delivered"
    assert [item["id"] for item in repository.list_inbox(sess.id)] == [first]

    assert repository.cancel_input(sess.id, first) is True
    assert repository.list_inbox(sess.id) == []
    resolved = repository.list_inbox(sess.id, include_resolved=True)
    assert {item["id"]: item["status"] for item in resolved}[first] == "cancelled"


def test_permission_and_form_repository(store) -> None:
    sess = _session(store)
    permission = repository.create_permission(
        sess.id,
        "perm_1",
        "edit",
        ["E:/AgentSuper/a.py"],
        save=["session"],
        metadata={"reason": "write"},
        source={"type": "tool", "message_id": "msg_1", "id": "call_1"},
        message="allow?",
    )
    assert permission["status"] == "pending"
    assert permission["resources"] == ["E:/AgentSuper/a.py"]
    assert repository.list_permissions(sess.id)[0]["id"] == "perm_1"
    assert repository.reply_permission(sess.id, "perm_1", "once") is True
    assert repository.list_permissions(sess.id) == []
    assert repository.list_permissions(sess.id, status=None)[0]["reply"] == "once"

    repository.save_permission_rule("u1", "edit", "E:/AgentSuper/a.py", "allow")
    repository.save_permission_rule("u1", "edit", "E:/AgentSuper/a.py", "deny")
    saved = repository.list_saved_permissions("u1")
    assert len(saved) == 1
    assert saved[0]["effect"] == "deny"

    form = repository.create_form(
        sess.id, "form_1", [{"id": "pm", "type": "select", "label": "PM"}], title="选择"
    )
    assert form["state"] == "pending"
    assert repository.list_forms(sess.id)[0]["id"] == "form_1"
    assert repository.reply_form(sess.id, "form_1", {"pm": "pnpm"}) is True
    assert repository.list_forms(sess.id, state="replied")[0]["response"] == {"pm": "pnpm"}


def test_connection_migrates_old_schema_without_new_columns(store) -> None:
    _get_db(store).close()
    conn = sqlite3.connect(store)
    try:
        for index in ("idx_inputs_session_status", "uq_parts_message_ordinal", "idx_parts_message_ordinal"):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        for table, columns in {
            "sessions": ("outcome", "metadata", "revert_state", "time_busy", "time_idle", "time_viewed"),
            "session_messages": ("time_completed",),
            "message_parts": ("ordinal", "time_updated"),
            "session_inputs": ("type", "status", "time_delivered"),
        }.items():
            for column in columns:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("DELETE FROM schema_migrations WHERE id = ?", (MIGRATION_ID,))
        conn.commit()
    finally:
        conn.close()

    migrated = _get_db(store)
    try:
        part_cols = {row[1] for row in migrated.execute("PRAGMA table_info(message_parts)")}
        input_cols = {row[1] for row in migrated.execute("PRAGMA table_info(session_inputs)")}
        marker = migrated.execute(
            "SELECT id FROM schema_migrations WHERE id = ?", (MIGRATION_ID,)
        ).fetchone()
    finally:
        migrated.close()

    assert {"ordinal", "time_updated"} <= part_cols
    assert {"type", "status", "time_delivered"} <= input_cols
    assert marker is not None


def test_legacy_ordinal_backfill_is_idempotent(store) -> None:
    sess = _session(store)
    msg = repository.upsert_message(sess.id, "msg_legacy", "assistant", {})

    conn = sqlite3.connect(store)
    try:
        conn.execute("DROP INDEX IF EXISTS uq_parts_message_ordinal")
        conn.execute("DROP INDEX IF EXISTS idx_parts_message_ordinal")
        conn.execute("DELETE FROM schema_migrations WHERE id = ?", (MIGRATION_ID,))
        for part_id in ("p1", "p2"):
            conn.execute(
                "INSERT INTO message_parts"
                " (id, session_id, message_id, type, ordinal, data, time_created)"
                " VALUES (?,?,?,?,?,?,?)",
                (part_id, sess.id, msg.id, "text", 0, "{}", 1),
            )
        conn.commit()
        report = migrate_v2(conn, force=True)
        ordinals = [
            row[0]
            for row in conn.execute(
                "SELECT ordinal FROM message_parts WHERE message_id = ? ORDER BY ordinal",
                (msg.id,),
            )
        ]
        second = migrate_v2(conn, force=True)
    finally:
        conn.close()

    assert report["part_ordinals_changed"] >= 1
    assert ordinals == [0, 1]
    assert report["validation"]["duplicate_part_ordinals"] == 0
    assert second["validation"]["duplicate_part_ordinals"] == 0


def test_snapshot_roundtrip_is_persistent(store) -> None:
    sess = _session(store)
    descriptor = {"files": [{"path": "a.txt", "old": None}], "before_count": 1}
    repository.save_snapshot(sess.id, "am_1", descriptor)
    assert repository.get_snapshot(sess.id, "am_1") == descriptor

    repository.save_snapshot(sess.id, "am_1", {"files": [], "before_count": 0})
    assert repository.get_snapshot(sess.id, "am_1") == {"files": [], "before_count": 0}
    assert repository.get_snapshot(sess.id, "missing") is None