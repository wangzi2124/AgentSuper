"""G-08: provider/model variant grammar and merge."""

from __future__ import annotations

import pytest

from models import catalog
from models.variants import UnknownVariant, apply_variant, find_variant, split_variant


def test_split_variant():
    assert split_variant("p/m") == ("p/m", "")
    assert split_variant("p/m#fast") == ("p/m", "fast")
    with pytest.raises(UnknownVariant):
        split_variant("p/m#")


def test_unknown_variant_raises():
    with pytest.raises(UnknownVariant):
        find_variant({"p": {"variants": {"fast": {}}}}, None, "p/m", "slow")


def test_apply_variant_merges_headers_lowercase():
    base = {"options": {"think": False}, "headers": {"X-Old": "a"}}
    out = apply_variant(
        base,
        {"settings": {"num_ctx": 4096}, "headers": {"X-New": "b"}, "body": {"b": 1}},
        variant="fast",
    )
    assert out["options"]["num_ctx"] == 4096
    assert out["headers"] == {"x-old": "a", "x-new": "b"}
    assert out["body"] == {"b": 1}
    assert out["variant"] == "fast"
    assert base["options"] == {"think": False}


def test_provider_api_resolves_variant(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "read_providers",
        lambda: {
            "p": {
                "type": "openai",
                "api_base": "http://local",
                "api_key": "k",
                "timeout": 42,
                "headers": {"X-Provider": "yes"},
                "body": {"feature": True},
                "package": "openai",
                "variants": {"fast": {"settings": {"think": True}}},
            }
        },
    )
    monkeypatch.setattr(catalog, "lookup", lambda mid: None)
    result = catalog.provider_api("p/model#fast")
    assert result["variant"] == "fast"
    assert result["options"]["think"] is True
    assert result["headers"] == {"X-Provider": "yes"}
    assert result["body"] == {"feature": True}
    assert result["timeout"] == 42
    with pytest.raises(UnknownVariant):
        catalog.provider_api("p/model#missing")
