# -*- coding: utf-8 -*-
"""S6/E2 SSRF 防护：出站 HTTP 地址校验安全用例。

覆盖 validate_http_url / is_internal_ip / allow_internal：
  - 私有 / 环回 / 链路本地 / 保留 / 云元数据 → 拦截
  - 协议仅 http/https；缺 host 拦截
  - 公网地址放行；SSRF_ALLOW_INTERNAL=true 时内网放行
运行：pytest tests/test_ssrf.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.utils import ssrf


def _clear_env(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOW_INTERNAL", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/api/chat",
        "http://localhost:8000/x",
        "http://192.168.1.10/",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://169.254.0.1/",
        "http://[::1]:8000/",
        "http://metadata.google.internal/",
        "http://foo.internal/",
        "http://bar.local/",
        "http://baz.lan/",
    ],
)
def test_internal_urls_blocked(url, monkeypatch):
    _clear_env(monkeypatch)
    err = ssrf.validate_http_url(url)
    assert err != "", url


def test_public_urls_allowed(monkeypatch):
    _clear_env(monkeypatch)
    for url in ("https://example.com/", "http://example.org/api", "https://8.8.8.8/"):
        assert ssrf.validate_http_url(url) == "", url


def test_scheme_and_host_validation(monkeypatch):
    _clear_env(monkeypatch)
    assert ssrf.validate_http_url("ftp://example.com/x") != ""
    assert ssrf.validate_http_url("file:///etc/passwd") != ""
    assert ssrf.validate_http_url("http://") != ""
    assert ssrf.validate_http_url("not a url") != ""


def test_allow_internal_bypass(monkeypatch):
    monkeypatch.setenv("SSRF_ALLOW_INTERNAL", "true")
    assert ssrf.allow_internal() is True
    assert ssrf.validate_http_url("http://127.0.0.1:8080/") == ""


def test_check_url_raises(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(ValueError):
        ssrf.check_url("http://192.168.0.1/")
    ssrf.check_url("https://example.com/")  # 不抛


@pytest.mark.parametrize(
    ("ip", "internal"),
    [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("172.16.5.5", True),
        ("192.168.9.9", True),
        ("169.254.169.254", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("8.8.8.8", False),
        ("1.2.3.4", False),
        ("240.0.0.1", True),  # 240/4 保留段（class E）→ ipaddress.is_reserved → 拦截
    ],
)
def test_is_internal_ip(ip, internal):
    assert ssrf.is_internal_ip(ip) is internal