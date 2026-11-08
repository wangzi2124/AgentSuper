"""G-17: HTTP skill catalog parsing and installation."""

from __future__ import annotations

import httpx

from app.skills import catalog


class _Resp:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_fetch_catalog_normalizes(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: _Resp(
            {"skills": [{"name": "a", "url": "https://x/a.md", "description": "d"}, {"bad": 1}]}
        ),
    )
    items = catalog.fetch_catalog("https://x/catalog.json")
    assert items == [{"name": "a", "description": "d", "url": "https://x/a.md"}]


def test_install_catalog(tmp_path, monkeypatch):
    payloads = {
        "https://x/catalog.json": _Resp({"skills": [{"name": "a", "url": "https://x/a.md"}]}),
        "https://x/a.md": _Resp(text="# A\n"),
    }
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: payloads[url])
    result = catalog.install_catalog("https://x/catalog.json", tmp_path)
    assert result["installed"] == ["a"]
    assert (tmp_path / "a" / "SKILL.md").read_text(encoding="utf-8") == "# A\n"


def test_rejects_unsafe_name(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: _Resp(text="x"))
    try:
        catalog.install_skill("https://x/a", "../escape", tmp_path)
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("must reject")
