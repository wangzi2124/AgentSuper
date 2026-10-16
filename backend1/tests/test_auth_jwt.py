# -*- coding: utf-8 -*-
"""S5 身份校验：JWT 签发/验证安全用例。

覆盖 issue_token / verify_token：
  - 有效 token 通过（sub 绑定）
  - 伪造 token / 篡改签名 / 错误 sub / 过期 → 拒绝
  - 未配置 AUTH_TOKEN_SECRET 时全部拒绝（校验关闭语义）
运行：pytest tests/test_auth_jwt.py
"""
import base64
import hmac
import hashlib
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app import auth
from app.config import settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_token_valid(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token, exp = auth.issue_token("u-alice")
    assert auth.verify_token("u-alice", token) is True
    assert exp > time.time()


def test_forged_uid_rejected(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token, _ = auth.issue_token("u-alice")
    assert auth.verify_token("u-mallory", token) is False


def test_tampered_signature_rejected(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token, _ = auth.issue_token("u-alice")
    # 翻转最后一个签名字符 → 签名失配
    head, sig = token.rsplit(".", 1)
    flipped = ("A" if sig[-1] != "A" else "B") + sig[1:]
    assert auth.verify_token("u-alice", head + "." + flipped) is False


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "secret-A")
    token, _ = auth.issue_token("u-alice")
    monkeypatch.setattr(settings, "auth_secret", "secret-B")
    assert auth.verify_token("u-alice", token) is False


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    # 手工构造已过期 token（exp 在过去）
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps({"iss": "agent-super", "sub": "u-alice", "iat": 1, "exp": 1}, separators=(",", ":")).encode()
    )
    signing = f"{header}.{payload}"
    sig = hmac.new(
        "test-secret".encode(), signing.encode(), hashlib.sha256
    ).digest()
    token = f"{signing}.{_b64url(sig)}"
    assert auth.verify_token("u-alice", token) is False


def test_disabled_auth_rejects(monkeypatch):
    # 未配置 AUTH_TOKEN_SECRET → verify 一律 False（不误放行）
    monkeypatch.setattr(settings, "auth_secret", None)
    assert auth.enabled() is False
    assert auth.verify_token("u-alice", "whatever") is False