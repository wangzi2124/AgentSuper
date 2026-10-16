# -*- coding: utf-8 -*-
"""ssrf.py 剩余分支用例（补 test_ssrf.py）：allow_internal 取值、_host_is_internal
（空/元数据/localhost/后缀/裸 IP/域名解析/不可解析）、is_internal_ip 组播/未指定/
非法、validate_http_url 边界。

运行：pytest tests/test_ssrf_extra.py
"""
import os
import socket as socket_mod
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.utils import ssrf


@pytest.fixture
def no_internal(monkeypatch):
    monkeypatch.delenv("SSRF_ALLOW_INTERNAL", raising=False)


def test_allow_internal_values(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("SSRF_ALLOW_INTERNAL", v)
        assert ssrf.allow_internal() is True
    for v in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("SSRF_ALLOW_INTERNAL", v)
        assert ssrf.allow_internal() is False


def test_host_is_internal_empty_and_special(no_internal):
    assert ssrf._host_is_internal("") is True
    assert ssrf._host_is_internal("metadata.google.internal") is True
    assert ssrf._host_is_internal("100.100.100.200") is True
    assert ssrf._host_is_internal("localhost") is True
    assert ssrf._host_is_internal("foo.local") is True
    assert ssrf._host_is_internal("bar.internal") is True
    assert ssrf._host_is_internal("baz.lan") is True


def test_host_is_internal_bare_ip(no_internal):
    assert ssrf._host_is_internal("192.168.1.1") is True
    assert ssrf._host_is_internal("8.8.8.8") is False


def test_host_is_internal_domain_resolution(no_internal, monkeypatch):
    # 域名解析到内网 → 拦截
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda host, *a: [(2, 1, 6, "", ("169.254.169.254", 0))])
    assert ssrf._host_is_internal("evil.example.com") is True
    # 全公网 → 放行
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda host, *a: [(2, 1, 6, "", ("8.8.8.8", 0))])
    assert ssrf._host_is_internal("good.example.com") is False
    # 混合记录含内网 → 拦截
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda host, *a: [(2, 1, 6, "", ("8.8.8.8", 0)),
                                          (2, 1, 6, "", ("10.0.0.1", 0))])
    assert ssrf._host_is_internal("mixed.example.com") is True


def test_host_is_internal_unresolvable(no_internal, monkeypatch):
    def gaierror(*a):
        raise socket_mod.gaierror("no such host")
    monkeypatch.setattr(ssrf.socket, "getaddrinfo", gaierror)
    assert ssrf._host_is_internal("nonexistent.invalid") is False


def test_host_is_internal_getaddrinfo_malformed_entry(no_internal, monkeypatch):
    # info[4][0] 非 IP（异常时 continue）
    monkeypatch.setattr(ssrf.socket, "getaddrinfo",
                        lambda host, *a: [(2, 1, 6, "", ("not-an-ip", 0))])
    assert ssrf._host_is_internal("weird.example.com") is False


def test_is_internal_ip_extras(no_internal):
    assert ssrf.is_internal_ip("224.0.0.1") is True   # 组播
    assert ssrf.is_internal_ip("0.0.0.0") is True     # 未指定
    assert ssrf.is_internal_ip("not-an-ip") is False  # 非法
    assert ssrf.is_internal_ip("") is False


def test_validate_http_url_edge_cases(no_internal):
    assert ssrf.validate_http_url("") != ""
    assert ssrf.validate_http_url("http:///path") != ""  # 无 host
    assert ssrf.validate_http_url("javascript:alert(1)") != ""
    assert ssrf.validate_http_url("https://example.com/x") == ""
    # allow_internal 时内网放行（协议校验仍生效）
    import os as _os
    _os.environ["SSRF_ALLOW_INTERNAL"] = "true"
    try:
        assert ssrf.validate_http_url("http://127.0.0.1:8080/") == ""
        assert ssrf.validate_http_url("ftp://127.0.0.1/") != ""
    finally:
        _os.environ.pop("SSRF_ALLOW_INTERNAL", None)