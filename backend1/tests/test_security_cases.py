# -*- coding: utf-8 -*-
"""自动化安全用例补充（A6）：覆盖既有用例尚未触及的安全面。

既有安全用例：
  test_security_permission.py — S1 敏感文件 / S4 插件目录写保护 / .git 保护
  test_auth_jwt.py           — S5 JWT 签发/验证
  test_ssrf.py               — S6 出站 URL 校验（SSRF 协议/地址层）
  test_session_seq.py        — D1 并发 seq 原子性

本文件补全：
  S2  管理端点鉴权 require_admin（ADMIN_TOKEN Bearer / 未配置时仅本机）
  S3  会话隔离 IDOR：resolve_session_context 跨用户 403 / 不存在 404
  S5  AuthMiddleware：/api/* 必须携带 X-User-Id + X-Auth-Token，/api/auth/* 豁免
  S6  命令级 SSRF：curl/wget/ssh/ping 等出站命令的内网目标拦截
运行：pytest tests/test_security_cases.py
"""
import asyncio
import os
import sys
import types

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi import HTTPException

from app import auth
from app.api.deps import require_admin
from app.config import settings
from app.session import deps as session_deps
from app.tools.fstools.execv import _ssrf_check_command
from app.utils import ssrf


# ── S2 管理端点鉴权 require_admin ──────────────────────────────────────────


class _FakeReq:
    def __init__(self, headers=None, host: str = "127.0.0.1", uid: str = ""):
        self.headers = headers or {}
        self.client = types.SimpleNamespace(host=host)

    def add_uid(self):
        """供 S3 用例：覆盖 .headers 的 X-User-Id 注入点。"""
        return self


def test_admin_token_required_and_accepted(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "tok-secret")
    ok = _FakeReq(headers={"Authorization": "Bearer tok-secret"})
    require_admin(ok)  # 正确 token → 不抛

    bad = _FakeReq(headers={"Authorization": "Bearer wrong"})
    with pytest.raises(HTTPException) as e:
        require_admin(bad)
    assert e.value.status_code == 401


def test_admin_token_missing_rejected(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "tok-secret")
    with pytest.raises(HTTPException) as e:
        require_admin(_FakeReq(headers={}))
    assert e.value.status_code == 401
    # 非标准大小写的 Bearer 前缀也拒绝
    with pytest.raises(HTTPException) as e:
        require_admin(_FakeReq(headers={"Authorization": "bearer tok-secret"}))
    assert e.value.status_code == 401


def test_admin_no_token_localhost_only(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "")
    require_admin(_FakeReq(host="127.0.0.1"))  # 本机 → 放行
    require_admin(_FakeReq(host="::1"))
    with pytest.raises(HTTPException) as e:
        require_admin(_FakeReq(host="10.0.0.5"))  # 局域网 → 403
    assert e.value.status_code == 403


# ── S3 会话隔离 IDOR：跨用户访问 403 ────────────────────────────────────────


def _ctx_req(uid: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        headers={"X-User-Id": uid},
        app=types.SimpleNamespace(state=types.SimpleNamespace(session_service=object())),
    )


def _patch_repo(monkeypatch, session, project):
    monkeypatch.setattr(session_deps.repository, "get_session", lambda sid: session)
    monkeypatch.setattr(session_deps.repository, "get_project", lambda pid: project)
    monkeypatch.setattr(
        session_deps.repository, "resolve_project", lambda *a, **k: project
    )


def test_session_owned_by_same_user_ok(monkeypatch):
    session = types.SimpleNamespace(id="ses-1", user_id="u-alice", directory="", project_id="proj-x")
    project = types.SimpleNamespace(id="proj-x", root="/x")
    _patch_repo(monkeypatch, session, project)
    ctx = asyncio.run(session_deps.resolve_session_context(_ctx_req("u-alice"), "ses-1"))
    assert ctx.user_id == "u-alice"
    assert ctx.session_id == "ses-1"


def test_session_cross_user_forbidden(monkeypatch):
    session = types.SimpleNamespace(id="ses-1", user_id="u-alice", directory="", project_id="proj-x")
    _patch_repo(monkeypatch, session, types.SimpleNamespace(id="proj-x", root="/x"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(session_deps.resolve_session_context(_ctx_req("u-mallory"), "ses-1"))
    assert e.value.status_code == 403


def test_session_missing_404(monkeypatch):
    _patch_repo(monkeypatch, None, None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(session_deps.resolve_session_context(_ctx_req("u-alice"), "ses-nope"))
    assert e.value.status_code == 404


# ── S5 AuthMiddleware：/api/* 身份校验 ───────────────────────────────────────


def _mk_scope(method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {"type": "http", "method": method, "path": path, "headers": headers or []}


def _run_middleware(mw, scope) -> list[dict]:
    """下发下游响应 200，捕获 send 的所有消息。返回 sent 列表。"""
    sent: list[dict] = []

    async def downstream(s, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    # 以真 downstream 重建中间件（不能包 object()，passthrough 路径会调用 self.app）
    mw = auth.AuthMiddleware(downstream)
    asyncio.run(mw(scope, receive, send))
    return sent


def test_middleware_blocked_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", None)
    sent = _run_middleware(None, _mk_scope("POST", "/api/chat/x"))
    assert sent[0]["status"] == 200, "未配置 AUTH_TOKEN_SECRET 时应放行（不拦截）"


def test_middleware_requires_headers(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    sent = _run_middleware(None, _mk_scope("POST", "/api/chat/x"))
    assert sent[0]["status"] == 401


def test_middleware_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    scope = _mk_scope("POST", "/api/chat/x", [(b"x-user-id", b"u-alice"), (b"x-auth-token", b"forged")])
    sent = _run_middleware(None, scope)
    assert sent[0]["status"] == 401


def test_middleware_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token, _ = auth.issue_token("u-alice")
    scope = _mk_scope("POST", "/api/chat/x", [(b"x-user-id", b"u-alice"), (b"x-auth-token", token.encode())])
    sent = _run_middleware(None, scope)
    assert sent[0]["status"] == 200


def test_middleware_exempts_auth_route(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    sent = _run_middleware(None, _mk_scope("POST", "/api/auth/account/login"))
    assert sent[0]["status"] == 200  # /api/auth/* 豁免


def test_middleware_exempts_options_and_non_api(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    sent = _run_middleware(None, _mk_scope("OPTIONS", "/api/chat/x"))
    assert sent[0]["status"] == 200
    sent = _run_middleware(None, _mk_scope("GET", "/docs"))
    assert sent[0]["status"] == 200


def test_middleware_token_must_match_user(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token, _ = auth.issue_token("u-alice")
    # 用自己的 token 冒充他人 uid → 拒绝
    scope = _mk_scope("POST", "/api/chat/x", [(b"x-user-id", b"u-mallory"), (b"x-auth-token", token.encode())])
    sent = _run_middleware(None, scope)
    assert sent[0]["status"] == 401


# ── S6 命令级 SSRF：出站网络命令目标校验 ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_ssrf_env(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOW_INTERNAL", raising=False)


@pytest.mark.parametrize(
    "cmd",
    [
        "curl http://192.168.1.10/x",
        "curl -fsSL http://127.0.0.1:8000/x",
        "curl http://localhost:8000/x",
        "curl http://10.0.0.1/",
        "wget 10.0.0.1:8080/file",              # 裸 host:port
        "wget 192.168.0.2",                      # 裸 IP
        "ssh 172.16.0.5",                        # 裸目标主机
        "ping 127.0.0.1",
        "scp -r user@10.0.0.9:/a /b",
        "curl http://metadata.google.internal/",
    ],
)
def test_network_commands_blocked_internal(cmd):
    with pytest.raises(ValueError):
        _ssrf_check_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        "curl https://example.com/x",   # 解析走 DNS，宽容处理（不可达时告警不放行内网）
        "curl https://8.8.8.8/a",
        "wget http://1.2.3.4/f",
        "git clone https://github.com/x/y",   # 不在出站命令名单
        "ls /tmp",
        "python3 run.py",
        "curl -h",                             # 无目标
    ],
)
def test_network_commands_allow_public(cmd):
    _ssrf_check_command(cmd)  # 不抛


def test_command_ssrf_bypass_with_internal_allow(monkeypatch):
    monkeypatch.setenv("SSRF_ALLOW_INTERNAL", "true")
    _ssrf_check_command("curl http://127.0.0.1:8000/x")  # 调试放行
    _ssrf_check_command("ssh 10.0.0.1")


def test_command_ssrf_non_network_first_token_is_dns_safe():
    # 非网络命令即使参数含内网地址也不误伤
    _ssrf_check_command("echo http://192.168.1.1")