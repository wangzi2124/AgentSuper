"""Web search provider policy: selection, fallback and 429 cooldown (G-13).

Providers are declared, never assumed: a provider without credentials stays out
of the candidate list. A provider that reports rate limiting is cooled down for
``websearch_cooldown_seconds`` and skipped until then.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Any

logger = logging.getLogger(__name__)

SearchFn = Callable[[str, int], list[dict[str, str]]]

_RATE_LIMIT_MARKERS = ("429", "rate limit", "too many requests", "quota")
_cooldowns: dict[str, float] = {}


@dataclass(frozen=True)
class SearchProvider:
    name: str
    fn: SearchFn
    order: int


def _is_rate_limited(exc: BaseException) -> bool:
    text = f"{exc} {getattr(exc, 'status_code', '')}".lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def _available(provider: SearchProvider, now: float) -> bool:
    return _cooldowns.get(provider.name, 0.0) <= now


def candidates(
    *,
    provider: str = "auto",
    now: float | None = None,
    rng: random.Random | None = None,
) -> list[SearchProvider]:
    """Return configured providers in attempt order, skipping cooled ones."""
    from config import get_settings

    engines: list[SearchProvider] = []
    if getattr(get_settings(), "tavily_api_key", ""):
        from .web_tools import _tavily_search

        engines.append(SearchProvider("tavily", _tavily_search, 0))
    if getattr(get_settings(), "bing_api_key", ""):
        from .web_tools import _bing_search

        engines.append(SearchProvider("bing", _bing_search, 1))
    try:
        from .web_tools import _duckduckgo_search
    except ImportError:  # pragma: no cover - import cycle guard
        _duckduckgo_search = None
    if _duckduckgo_search is not None:
        engines.append(SearchProvider("duckduckgo", _duckduckgo_search, 2))

    choice = str(provider or "auto").strip().lower()
    if choice not in ("", "auto", "random"):
        selected = [item for item in engines if item.name == choice]
        if not selected:
            raise ValueError(f"unknown websearch provider: {choice}")
        engines = selected
    elif choice == "random" and engines:
        (rng or random).shuffle(engines)

    moment = time.monotonic() if now is None else now
    return [item for item in engines if _available(item, moment)]


def mark_cooldown(name: str, seconds: float, *, now: float | None = None) -> None:
    if seconds <= 0:
        return
    _cooldowns[name] = (time.monotonic() if now is None else now) + float(seconds)
    logger.warning("websearch provider %s cooling down for %.0fs", name, seconds)


def reset_cooldowns() -> None:
    _cooldowns.clear()


def cooldowns() -> dict[str, float]:
    return dict(_cooldowns)


def search(query: str, top_k: int) -> tuple[str, str]:
    """Run one search, returning ``(text, provider)`` or raising the last error."""
    from config import get_settings

    provider = str(getattr(get_settings(), "websearch_provider", "auto") or "auto")
    cooldown = float(getattr(get_settings(), "websearch_cooldown_seconds", 60.0) or 60.0)
    errors: list[str] = []
    chosen = candidates(provider=provider)
    if not chosen:
        raise RuntimeError("no websearch provider available")
    for item in chosen:
        try:
            results = item.fn(query, top_k)
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limited(exc):
                mark_cooldown(item.name, cooldown)
            errors.append(f"{item.name}: {exc}")
            continue
        if results:
            return _render(query, item.name, results), item.name
        errors.append(f"{item.name}: no results")
    raise RuntimeError("; ".join(errors) or "all websearch providers failed")


def _render(query: str, engine: str, results: list[dict[str, str]]) -> str:
    lines = [f"Web search results for: {query}", f"Engine: {engine}", ""]
    for index, item in enumerate(results, 1):
        lines.append(f"{index}. {item.get('title', '')}")
        lines.append(f"   URL: {item.get('url', '')}")
        lines.append(f"   {item.get('content', '')}")
        lines.append("")
    return "\n".join(lines).strip()


__all__ = [
    "SearchProvider",
    "candidates",
    "cooldowns",
    "mark_cooldown",
    "reset_cooldowns",
    "search",
]
