"""G-13: websearch provider selection, fallback and cooldown."""

from __future__ import annotations

import random

import pytest

from config import settings
from execution.tools import web_tools, websearch_policy


def test_provider_selection_respects_credentials(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.setattr(settings, "bing_api_key", "")
    names = [item.name for item in websearch_policy.candidates(provider="auto")]
    assert names == ["duckduckgo"]
    with pytest.raises(ValueError):
        websearch_policy.candidates(provider="tavily")


def test_random_order_is_seeded(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "x")
    monkeypatch.setattr(settings, "bing_api_key", "y")
    first = [p.name for p in websearch_policy.candidates(provider="random", rng=random.Random(1))]
    second = [p.name for p in websearch_policy.candidates(provider="random", rng=random.Random(1))]
    assert first == second
    assert set(first) == {"tavily", "bing", "duckduckgo"}


def test_cooldown_skips_provider(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "x")
    monkeypatch.setattr(settings, "bing_api_key", "")
    websearch_policy.reset_cooldowns()
    websearch_policy.mark_cooldown("tavily", 60, now=100)
    names = [p.name for p in websearch_policy.candidates(provider="auto", now=110)]
    assert "tavily" not in names
    assert "duckduckgo" in names
    names_later = [p.name for p in websearch_policy.candidates(provider="auto", now=200)]
    assert "tavily" in names_later
    websearch_policy.reset_cooldowns()


def test_search_falls_back_after_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "x")
    monkeypatch.setattr(settings, "bing_api_key", "y")
    monkeypatch.setattr(settings, "websearch_provider", "auto")
    monkeypatch.setattr(settings, "websearch_cooldown_seconds", 60)
    websearch_policy.reset_cooldowns()

    def limited(query, top_k):
        raise RuntimeError("429 Too Many Requests")

    def ok(query, top_k):
        return [{"title": "t", "url": "https://example.com", "content": "c"}]

    monkeypatch.setattr(web_tools, "_tavily_search", limited)
    monkeypatch.setattr(web_tools, "_bing_search", ok)
    text, provider = websearch_policy.search("q", 3)
    assert provider == "bing"
    assert "Engine: bing" in text
    assert "tavily" not in [p.name for p in websearch_policy.candidates(provider="auto")]
