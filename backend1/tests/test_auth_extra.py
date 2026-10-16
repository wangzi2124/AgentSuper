# -*- coding: utf-8 -*-
"""auth.py 剩余分支用例（补 test_auth_jwt.py 的 JWT 之外）：用户注册/设备校验/
账号注册/登录/信息、持久化加载与迁移、AuthMiddleware 放行与 401。

运行：pytest tests/test_auth_extra.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.auth as auth
from app.config import settings


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    monkeypatch.setattr(settings, "auth_users_path", str(tmp_path / "users.json"))
    auth._users_cache = None
    yield
    auth._users_cache = None


# ── _load_users / 持久化迁移 ───────────────────────────────────────────────

def test_load_users_empty(env):
    assert auth._load_users() == {}


def test_load_users_legacy_migration(env, tmp_path):
    p = tmp_path / "users.json"
    p.write_text('{"u-old": "plain-hash-string"}', encoding="utf-8")
    users = auth._load_users()
    assert users["u-old"] == {"type": "device", "device_hash": "plain-hash-string", "created_at": 0}
    # 已迁移落盘为新格式
    data = auth._users_path().read_text(encoding="utf-8")
    assert '"type": "device"' in data


def test_load_users_corrupt(env, tmp_path, caplog):
    (tmp_path / "users.json").write_text("{bad", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert auth._load_users() == {}
    assert "auth users load failed" in caplog.text


# ── register / verify_device ───────────────────────────────────────────────

def test_register_validations(env):
    assert auth.register("", "s") == (False, "user_id 与 device_secret 不能为空")
    assert auth.register("u1", "") == (False, "user_id 与 device_secret 不能为空")


def test_register_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", None)
    assert auth.register("u1", "s") == (False, "身份签名未启用（未配置 AUTH_TOKEN_SECRET）")


def test_register_success_and_idempotent(env):
    ok, err = auth.register("u-device", "secret1")
    assert ok and err == ""
    # 同 secret 幂等
    ok2, _ = auth.register("u-device", "secret1")
    assert ok2
    assert auth.verify_device("u-device", "secret1") is True
    assert auth.verify_device("u-device", "wrong") is False
    assert auth.verify_device("u-unknown", "secret1") is False


def test_register_conflict(env):
    auth.register("u-device", "secret1")
    ok, err = auth.register("u-device", "other-secret")
    assert ok is False
    assert "已被其他设备注册" in err


def test_verify_device_guards(env):
    assert auth.verify_device("u1", "") is False
    assert auth.verify_device("", "s") is False


# ── 账号 ───────────────────────────────────────────────────────────────────

def test_register_account_validation(env):
    assert auth.register_account("ab", "pass123")[1] == "用户名长度需在 3-32 个字符之间"
    assert auth.register_account("bad name!", "pass123")[1] == "用户名只能包含字母、数字、下划线或连字符"
    assert auth.register_account("goodname", "123")[1] == "密码至少 6 位"


def test_register_account_success_and_login(env):
    ok, _, uid = auth.register_account("Alice", "secret123")
    assert ok and uid.startswith("u-")
    # 重复注册
    ok2, err, _ = auth.register_account("alice", "secret123")
    assert ok2 is False and "已被注册" in err
    # 登录
    assert auth.authenticate_account("ALICE", "secret123") == uid
    assert auth.authenticate_account("Alice", "wrong") == ""
    assert auth.authenticate_account("nobody", "secret123") == ""


def test_authenticate_account_guards(env):
    assert auth.authenticate_account("", "p") == ""
    assert auth.authenticate_account("u", "") == ""


def test_account_info(env):
    assert auth.account_info("ghost") is None
    _, _, uid = auth.register_account("Bob", "secret123")
    info = auth.account_info(uid)
    assert info["account_type"] == "account" and info["username"] == "Bob"
    auth.register("u-dev", "s")
    info2 = auth.account_info("u-dev")
    assert info2["account_type"] == "device" and info2["username"] == "u-dev"


# ── AuthMiddleware ─────────────────────────────────────────────────────────

def _scope(path, method="GET", headers=None, type="http"):
    return {
        "type": type, "path": path, "method": method,
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }


class _App:
    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope)


async def _run(mw, scope):
    sent = []

    async def send(msg):
        sent.append(msg)
    await mw(scope, None, send)
    return sent


@pytest.mark.asyncio
async def test_middleware_non_http_or_disabled(env, monkeypatch):
    app = _App()
    monkeypatch.setattr(settings, "auth_secret", None)
    mw = auth.AuthMiddleware(app)
    await mw({"type": "websocket"}, None, None)
    assert app.calls  # 直接放行


@pytest.mark.asyncio
async def test_middleware_bypass_paths(env):
    app = _App()
    mw = auth.AuthMiddleware(app)
    await mw(_scope("/health"), None, None)
    await mw(_scope("/api/auth/register"), None, None)
    await mw(_scope("/api/x", method="OPTIONS"), None, None)
    assert len(app.calls) == 3


@pytest.mark.asyncio
async def test_middleware_401_missing(env):
    app = _App()
    mw = auth.AuthMiddleware(app)
    sent = await _run(mw, _scope("/api/chat"))
    assert sent[0]["status"] == 401
    assert app.calls == []


@pytest.mark.asyncio
async def test_middleware_valid_token_passes(env):
    app = _App()
    mw = auth.AuthMiddleware(app)
    token, _ = auth.issue_token("u1")
    await mw(_scope("/api/chat", headers={"X-User-Id": "u1", "X-Auth-Token": token}), None, None)
    assert app.calls  # 通过


@pytest.mark.asyncio
async def test_middleware_wrong_uid_401(env):
    app = _App()
    mw = auth.AuthMiddleware(app)
    token, _ = auth.issue_token("u1")
    sent = await _run(mw, _scope("/api/chat", headers={"X-User-Id": "u2", "X-Auth-Token": token}))
    assert sent[0]["status"] == 401