"""Weather API TLS verification tests.

Locks the fix: weather.py previously built a context with check_hostname=False
+ CERT_NONE (disabling certificate validation, enabling MITM). The context must
use the platform default (CERT_REQUIRED + hostname checking).
"""

import ssl

from app.api import weather


def test_ssl_context_verifies_certificates():
    ctx = weather._create_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_fetch_json_uses_verified_context(monkeypatch):
    captured = {}

    class FakeResp:
        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False


    def fake_urlopen(req, timeout=0, context=None):
        captured["context"] = context
        return FakeResp()

    monkeypatch.setattr(weather.urllib.request, "urlopen", fake_urlopen)
    data = weather._fetch_json("https://example.com/", timeout=5)
    assert data == {"ok": True}
    assert captured["context"].verify_mode == ssl.CERT_REQUIRED