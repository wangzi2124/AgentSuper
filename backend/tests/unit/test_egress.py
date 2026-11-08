"""G-12: unified egress proxy/CA/timeout policy."""

from __future__ import annotations

import ssl

from config import settings
from net import egress


def _clear_env(monkeypatch):
    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_settings_proxy_wins_over_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://env:8080")
    monkeypatch.setattr(settings, "egress_proxy", "http://settings:9000")
    config = egress.egress_config()
    assert config.proxy == "http://settings:9000"


def test_env_proxy_fallback(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "egress_proxy", "")
    monkeypatch.setenv("https_proxy", "http://env:8080")
    assert egress.egress_config().proxy == "http://env:8080"


def test_no_proxy_and_timeout(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "egress_no_proxy", "localhost,127.0.0.1")
    monkeypatch.setattr(settings, "egress_timeout_seconds", 7)
    config = egress.egress_config()
    assert config.no_proxy.startswith("localhost")
    assert config.timeout == 7.0


def test_httpx_kwargs_and_ssl(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(settings, "egress_proxy", "http://p:1")
    monkeypatch.setattr(settings, "egress_verify", True)
    kwargs = egress.httpx_client_kwargs()
    assert kwargs["proxy"] == "http://p:1"
    assert kwargs["trust_env"] is False
    assert isinstance(kwargs["verify"], ssl.SSLContext)


def test_bad_ca_bundle_is_skipped(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    bad = tmp_path / "bad.pem"
    bad.write_text("not a cert", encoding="utf-8")
    monkeypatch.setattr(settings, "egress_ca_bundle", str(bad))
    assert isinstance(egress.ssl_context(), ssl.SSLContext)
