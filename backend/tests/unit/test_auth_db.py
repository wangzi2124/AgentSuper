"""用户身份落库（auth_users）与旧 JSON 一次性导入测试。"""

from __future__ import annotations

import json

import app.auth as auth
import app.session.db as session_db
from config import settings


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(session_db, "DB_PATH", tmp_path / "session.db")
    monkeypatch.setattr(session_db, "_pool", session_db._ConnectionPool(2))
    monkeypatch.setattr(auth, "_legacy_import_checked", False)
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_users_path", str(tmp_path / "auth_users.json"))


def test_legacy_json_imported_into_db_then_removed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    legacy = tmp_path / "auth_users.json"
    legacy.write_text(
        json.dumps(
            {
                "u-old": {
                    "type": "device",
                    "device_hash": auth._secret_hash("dev-secret"),
                    "created_at": 1,
                },
                "u-account": {
                    "type": "account",
                    "username": "OldUser",
                    "pw_salt": "00" * 16,
                    "pw_hash": "deadbeef",
                    "created_at": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    assert auth.verify_device("u-old", "dev-secret") is True
    assert legacy.exists() is False

    conn = session_db._get_db()
    try:
        row = conn.execute(
            "SELECT * FROM auth_users WHERE id = ?", ("u-account",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["username"] == "OldUser"
    assert row["username_lower"] == "olduser"
    assert row["type"] == "account"


def test_account_registered_in_db_not_json(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    legacy = tmp_path / "auth_users.json"

    ok, err, uid = auth.register_account("dbuser", "pass1234")
    assert ok, err
    assert legacy.exists() is False
    assert auth.authenticate_account("dbuser", "pass1234") == uid
    assert auth.authenticate_account("DBUSER", "pass1234") == uid
    assert auth.authenticate_account("dbuser", "wrong") == ""
    assert auth.register(uid, "device-secret")[0] is False

    conn = session_db._get_db()
    try:
        row = conn.execute("SELECT * FROM auth_users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["username"] == "dbuser"
    assert row["pw_hash"] != "pass1234"


def test_device_registered_in_db(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    ok, err = auth.register("u-dev", "dev-secret")
    assert ok, err
    assert auth.verify_device("u-dev", "dev-secret") is True
    assert auth.verify_device("u-dev", "other") is False
    assert auth.register("u-dev", "other")[0] is False
    assert auth.register("u-dev", "dev-secret")[0] is True
