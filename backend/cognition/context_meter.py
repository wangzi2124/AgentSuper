"""Single token-accounting module for context decisions.

Every caller that needs to know "how big is this context?" must cross this
interface: the router, the compactor, the overflow guard and the budget all
use the same estimate.  Media blocks are counted by a fixed per-image cost and
never by their base64 payload length.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from cognition.token_counter import (
    estimate_tokens,
    estimate_tools,
    token_counter_for,
    update_token_correction,
)

_TOKEN_SOURCE = "estimate"
_CALIBRATION_ALPHA = 0.3
_CALIBRATION_MIN = 1.0
_CALIBRATION_MAX = 2.0
_TEXT_BLOCK_TYPES = frozenset({"text", "input_text"})
_IMAGE_BLOCK_TYPES = frozenset({"image_url", "image", "input_image"})


@dataclass(frozen=True)
class TokenEstimate:
    """Token breakdown for one model view.

    ``media`` is a subset of ``messages``: it is reported for observability,
    not added again to ``total``.
    """

    total: int
    messages: int
    system: int
    tools: int
    media: int
    source: Literal["native", "tiktoken", "heuristic", "usage", "estimate"] = _TOKEN_SOURCE


def _image_tokens() -> int:
    try:
        from config import get_settings

        return max(0, int(getattr(get_settings(), "image_token_estimate", 1_600) or 0))
    except Exception:  # noqa: BLE001 - config unavailable in tests/tools
        return 1_600


def _content_estimate(content: Any, counter=estimate_tokens) -> tuple[int, int]:
    """Return ``(text_tokens, media_tokens)`` without stringifying blobs."""
    text_parts: list[str] = []
    images = 0
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            block_type = str(part.get("type") or "")
            if block_type in _TEXT_BLOCK_TYPES:
                text_parts.append(str(part.get("text") or ""))
            elif block_type in _IMAGE_BLOCK_TYPES:
                images += 1
            elif isinstance(part.get("text"), str):
                text_parts.append(part["text"])
    elif isinstance(content, dict):
        if str(content.get("type") or "") in _IMAGE_BLOCK_TYPES:
            images += 1
        elif isinstance(content.get("text"), str):
            text_parts.append(content["text"])
    text = "\n".join(p for p in text_parts if p)
    return (counter(text) if text else 0), images * _image_tokens()


def _tool_call_tokens(call: Any, counter=estimate_tokens) -> int:
    if isinstance(call, dict):
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(fn.get("name") or call.get("name") or "")
        args: Any = fn.get("arguments", call.get("args"))
    else:
        name = str(getattr(call, "name", "") or "")
        args = getattr(call, "args", None)
    if isinstance(args, str):
        raw = args
    else:
        try:
            raw = json.dumps(args or {}, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            raw = ""
    return counter(name) + counter(raw) + 8


def _message_parts(msg: Any) -> tuple[Any, list[Any]]:
    if isinstance(msg, dict):
        return msg.get("content", ""), list(msg.get("tool_calls") or [])
    content = getattr(msg, "content", "")
    calls = getattr(msg, "tool_calls", None)
    if calls is None:
        calls = (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls")
    return content, list(calls or [])


def estimate_message_tokens(msg: Any, counter=estimate_tokens) -> tuple[int, int]:
    """Return ``(tokens, media_tokens)`` for one message."""
    content, tool_calls = _message_parts(msg)
    text_tokens, media = _content_estimate(content, counter)
    extra = sum(_tool_call_tokens(c, counter) for c in tool_calls)
    # Per-message framing (role, separators) mirrors token_counter's overhead.
    return text_tokens + extra + 4, media


def message_fingerprint(messages: Sequence[Any]) -> str:
    """Stable content-aware key used to cache repeated estimates.

    Ids alone are not enough: tool-loop dicts often carry only ``_id`` and the
    same conversation can be re-estimated after content edits. Hashing the
    serialized message keeps the cache correct without tokenizing twice.
    """
    digest = hashlib.sha1()
    for msg in messages:
        ident = getattr(msg, "id", None)
        if isinstance(msg, dict):
            ident = msg.get("id") or msg.get("_id") or msg.get("tool_call_id")
        digest.update(str(ident or "").encode("utf-8"))
        digest.update(b"\x00")
        if ident:
            # Stable ids are unique per conversation; skip serializing large
            # tool output / base64 payloads on every cache lookup.
            continue
        try:
            blob = json.dumps(msg, ensure_ascii=False, default=str, sort_keys=True)
        except Exception:  # noqa: BLE001
            blob = str(msg)
        digest.update(blob.encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


class ContextMeter:
    """Deep module: one interface for every context budget decision."""

    _MAX_CACHE = 256

    def __init__(self) -> None:
        self._cache: dict[str, TokenEstimate] = {}
        self._ratios: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _tools_key(tools: Sequence[dict] | None) -> str:
        if not tools:
            return ""
        try:
            blob = json.dumps(list(tools), ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            blob = str(len(tools))
        return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()

    def ratio(self, model_id: str) -> float:
        """Per-model calibration factor (1.0 = no correction)."""
        mid = str(model_id or "")
        if not mid:
            return 1.0
        with self._lock:
            return float(self._ratios.get(mid, 1.0))

    def observe_model(self, model_id: str, estimated: int, actual: int) -> None:
        """Update the per-model estimate ratio from provider-reported usage."""
        mid = str(model_id or "")
        estimated = int(estimated or 0)
        actual = int(actual or 0)
        if not mid or estimated <= 0 or actual <= 0:
            return
        observed = max(
            _CALIBRATION_MIN, min(_CALIBRATION_MAX, actual / estimated)
        )
        with self._lock:
            previous = self._ratios.get(mid)
            if previous is None:
                self._ratios[mid] = observed
            else:
                self._ratios[mid] = (
                    previous * (1.0 - _CALIBRATION_ALPHA)
                    + observed * _CALIBRATION_ALPHA
                )
            prefix = f"{mid}|"
            self._cache = {
                key: value
                for key, value in self._cache.items()
                if not key.startswith(prefix)
            }

    def reset_calibration(self) -> None:
        with self._lock:
            self._ratios.clear()
        self.clear()

    def estimate(
        self,
        messages: Sequence[Any],
        *,
        system: str = "",
        tools: Sequence[dict] | None = None,
        model_id: str = "",
        use_cache: bool = False,
    ) -> TokenEstimate:
        cache_key = ""
        if use_cache:
            cache_key = "|".join(
                [
                    str(model_id or ""),
                    message_fingerprint(messages),
                    str(len(messages)),
                    str(hash(system)),
                    self._tools_key(tools),
                ]
            )
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        counter = token_counter_for(model_id)
        msg_total = 0
        media_total = 0
        for msg in messages:
            tokens, media = estimate_message_tokens(msg, counter)
            msg_total += tokens
            media_total += media
        system_tokens = counter(system) if system else 0
        tool_tokens = estimate_tools(list(tools) if tools else None, counter)
        ratio = self.ratio(model_id)

        def _scale(value: int) -> int:
            return int(round(int(value or 0) * ratio))

        result = TokenEstimate(
            total=_scale(msg_total + system_tokens + tool_tokens),
            messages=_scale(msg_total),
            system=_scale(system_tokens),
            tools=_scale(tool_tokens),
            media=media_total,
        )
        if use_cache and cache_key:
            with self._lock:
                if len(self._cache) >= self._MAX_CACHE and self._cache:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = result
        return result

    def observe(self, model_id: str, estimated: int, actual_prompt_tokens: int) -> None:
        """Calibrate the estimator from Provider usage (global correction)."""
        if not model_id:
            return
        update_token_correction(int(estimated), int(actual_prompt_tokens))

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_METER = ContextMeter()


def get_meter() -> ContextMeter:
    return _METER


__all__ = [
    "ContextMeter",
    "TokenEstimate",
    "estimate_message_tokens",
    "get_meter",
    "message_fingerprint",
]
