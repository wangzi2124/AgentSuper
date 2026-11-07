"""Model Runtime: one deep module for resolving and executing a model call.

The module owns three concerns that used to leak across ``models/router.py``,
``models/catalog.py``, ``models/context_guard.py`` and ``models/stream.py``:

- endpoint/credential resolution (proxy, provider registry, config fallback);
- local Ollama window selection, warmth (``keep_alive``), and sticky ``num_ctx``;
- the transport call itself (``complete`` / ``stream``).

Callers keep using ``TierModel`` as a facade. Resolution is cached for the
duration of a :class:`Turn Scope`; background callers without a scope resolve
once per call. See ``docs/adr/0001-model-runtime-ownership.md``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import litellm

from config import settings

from .context_guard import (
    FitResult,
    is_overflow_error,
    record_actual_usage,
    refit_after_overflow,
)
from .types import ChatResult, ToolCall

logger = logging.getLogger(__name__)

litellm.drop_params = True

TIER_ORDER = ("simple", "medium", "complex", "reasoning")

TIER_MAP: dict[str, str] = {
    "simple": "tier-simple",
    "medium": "tier-medium",
    "complex": "tier-complex",
    "reasoning": "tier-complex",
    "embedding": "embedding",
    "auto": "smart-router",
}

DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT = 120
LOCAL_OLLAMA_TIMEOUT = 300
LOCAL_OLLAMA_MAX_TOKENS = 4096
DEFAULT_MAX_RETRIES = 2

COMPRESS_MODEL = "compress-model"

# The model selected for the current turn (front-end selector / model manager).
_request_model_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_model", default=None
)
_request_session_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_session", default=None
)


def set_request_model(model: str | None) -> contextvars.Token:
    return _request_model_var.set(model or None)


def reset_request_model(token: contextvars.Token) -> None:
    try:
        _request_model_var.reset(token)
    except ValueError:  # generator resumed/closed from another context
        _request_model_var.set(None)


def request_model() -> str | None:
    return _request_model_var.get()


def set_request_session(session_id: str | None) -> contextvars.Token:
    """Expose the turn's session id to provider cache/session-affinity hints."""
    return _request_session_var.set(session_id or None)


def reset_request_session(token: contextvars.Token) -> None:
    try:
        _request_session_var.reset(token)
    except ValueError:  # generator resumed/closed from another context
        _request_session_var.set(None)


def request_session() -> str | None:
    return _request_session_var.get()


# ── Turn Scope ────────────────────────────────────────────────────────────────
# A turn snapshots its runtime resolutions so repeated node/model lookups do not
# re-open the catalog DB. The scope is opened by the turn runner, not by this
# module, to keep the models layer independent from harness/app.
_scope_var: contextvars.ContextVar[dict[str, "ModelRuntime"] | None] = (
    contextvars.ContextVar("model_runtime_scope", default=None)
)


def begin_scope() -> contextvars.Token:
    """Open a Turn Scope for runtime-resolution caching."""
    return _scope_var.set({})


def end_scope(token: contextvars.Token) -> None:
    try:
        _scope_var.reset(token)
    except ValueError:  # pragma: no cover - token from another context
        _scope_var.set(None)


def current_scope() -> dict[str, "ModelRuntime"] | None:
    return _scope_var.get()


def reset_scope() -> None:
    """Test helper: drop the current scope."""
    _scope_var.set(None)


# ── Local Ollama window / warmth state ────────────────────────────────────────
_RUNTIME_CTX_TTL = 30.0
_LOCAL_PROBE_TIMEOUT = 3.0
_runtime_ctx_last: dict[str, int] = {}
_ps_cache: dict[str, int] | None = None
_ps_cache_at: float = 0.0
_refresh_inflight = False
_runtime_lock = threading.Lock()
_sticky_num_ctx: dict[str, int] = {}

_stats_lock = threading.Lock()
_stats: dict[str, int] = {
    "warm_ok": 0,
    "warm_failed": 0,
    "cold_misses": 0,
    "window_refresh_ok": 0,
    "window_refresh_failed": 0,
    "num_ctx_expansions": 0,
}


def _bump(name: str, amount: int = 1) -> None:
    with _stats_lock:
        _stats[name] = int(_stats.get(name, 0)) + int(amount)


def runtime_stats() -> dict[str, int]:
    with _stats_lock:
        return dict(_stats)


def reset_state() -> None:
    """Test helper: clear window/warmth caches and counters."""
    global _ps_cache, _ps_cache_at, _refresh_inflight
    with _runtime_lock:
        _runtime_ctx_last.clear()
        _ps_cache = None
        _ps_cache_at = 0.0
        _refresh_inflight = False
    with _stats_lock:
        for key in _stats:
            _stats[key] = 0
    _sticky_num_ctx.clear()


def _ollama_base_url() -> str:
    host = settings.ollama_api_base or settings.ollama_host or "http://localhost:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def _ollama_native_base(api_base: str | None) -> str:
    base = str(api_base or "").strip().rstrip("/")
    for suffix in ("/api/chat", "/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    return base


def _fetch_ps() -> dict[str, int]:
    """Synchronous ``/api/ps`` snapshot. Only called off the model-call path."""
    out: dict[str, int] = {}
    try:
        from .catalog import local_endpoint_alive

        if not local_endpoint_alive(_ollama_base_url()):
            return out
        req = urllib.request.Request(
            f"{_ollama_base_url()}/api/ps",
            headers={"User-Agent": "agentsuper/0.1"},
        )
        with urllib.request.urlopen(req, timeout=_LOCAL_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for model in data.get("models") or []:
            try:
                ctx = max(0, int(model.get("context_length") or 0))
            except (TypeError, ValueError):
                ctx = 0
            for name in {str(model.get("name") or ""), str(model.get("model") or "")}:
                if name:
                    out[name] = ctx
    except Exception:  # noqa: BLE001 - unreachable Ollama is not an error
        out = {}
    return out


def _store_ps(snapshot: dict[str, int]) -> None:
    global _ps_cache, _ps_cache_at
    with _runtime_lock:
        _ps_cache = dict(snapshot)
        _ps_cache_at = time.time()


def refresh_local_windows() -> dict[str, int]:
    """Probe ``/api/ps`` now (blocking). Used by warm-up and explicit refreshes."""
    global _refresh_inflight
    snapshot = _fetch_ps()
    _store_ps(snapshot)
    with _runtime_lock:
        _refresh_inflight = False
    if snapshot:
        _bump("window_refresh_ok")
    else:
        _bump("window_refresh_failed")
    return snapshot


def _kick_refresh() -> None:
    global _refresh_inflight
    with _runtime_lock:
        if _refresh_inflight:
            return
        _refresh_inflight = True
    try:
        threading.Thread(target=refresh_local_windows, daemon=True).start()
    except Exception:  # noqa: BLE001
        with _runtime_lock:
            _refresh_inflight = False
        logger.debug("local window refresh thread failed", exc_info=True)


def _snapshot_fresh() -> bool:
    with _runtime_lock:
        return _ps_cache is not None and (time.time() - _ps_cache_at) < _RUNTIME_CTX_TTL


def _ps_snapshot(force: bool = False) -> dict[str, int]:
    """Return the last-known snapshot; optionally refresh synchronously."""
    if force:
        return refresh_local_windows()
    if not _snapshot_fresh():
        _kick_refresh()
    with _runtime_lock:
        return dict(_ps_cache or {})


def _model_name(model_id: str) -> str:
    try:
        from .catalog import get_ollama_model_name

        return get_ollama_model_name(model_id)
    except Exception:  # noqa: BLE001
        raw = str(model_id or "")
        return raw.split("/", 1)[1] if "/" in raw else raw


def _normalize(model_id: str | None) -> str:
    try:
        from .catalog import normalize_model

        return normalize_model(model_id) or str(model_id or "")
    except Exception:  # noqa: BLE001
        return str(model_id or "")


def _is_ollama(model_id: str | None) -> bool:
    try:
        from .catalog import is_ollama_model

        return is_ollama_model(model_id)
    except Exception:  # noqa: BLE001
        return str(model_id or "").lower().startswith(("ollama/", "ollama_chat/"))


def probe_runtime_context_length(model_id: str | None, force: bool = False) -> int:
    """Resolve the runtime window for an Ollama model (may probe synchronously)."""
    mid = _normalize(model_id)
    if not _is_ollama(mid):
        return 0
    name = _model_name(mid)
    if force or not _snapshot_fresh():
        snapshot = _ps_snapshot(force=True)
    else:
        snapshot = _ps_snapshot()
    ctx = int(snapshot.get(name) or 0)
    with _runtime_lock:
        if ctx > 0:
            _runtime_ctx_last[mid] = ctx
        resolved = int(_runtime_ctx_last.get(mid) or 0)
    _observe_window(mid, resolved)
    return resolved


def forget_runtime_context(model_id: str | None, *, reprobe: bool = True) -> int:
    mid = _normalize(model_id)
    if not _is_ollama(mid):
        return 0
    with _runtime_lock:
        _runtime_ctx_last.pop(mid, None)
    _sticky_num_ctx.pop(mid, None)
    if reprobe:
        return probe_runtime_context_length(mid, force=True)
    return 0


def effective_window(model_id: str | None, fallback: int = 0) -> int:
    """Non-blocking window resolution used on the model-call path."""
    mid = _normalize(model_id)
    if not mid:
        return max(0, int(fallback or 0))
    if not _is_ollama(mid):
        try:
            from .catalog import read_limits

            return int(read_limits(mid).get("context_length") or 0) or max(
                0, int(fallback or 0)
            )
        except Exception:  # noqa: BLE001
            return max(0, int(fallback or 0))

    try:
        from .catalog import _explicit_context_override

        explicit = int(_explicit_context_override(mid) or 0)
    except Exception:  # noqa: BLE001
        explicit = 0
    if explicit > 0:
        return explicit

    if not _snapshot_fresh():
        _bump("cold_misses")
        _kick_refresh()

    with _runtime_lock:
        last = int(_runtime_ctx_last.get(mid) or 0)
        snapshot = dict(_ps_cache or {})
    runtime = int(snapshot.get(_model_name(mid)) or 0)
    if runtime > 0:
        with _runtime_lock:
            _runtime_ctx_last[mid] = runtime
        _observe_window(mid, runtime)
        return runtime
    if last > 0:
        return last
    if not fallback:
        fallback = int(getattr(settings, "ollama_num_ctx", 0) or 0)
    return max(0, int(fallback or 0))


def runtime_context_lengths(model_ids: list[str]) -> dict[str, int]:
    """Display helper: runtime window per model without blocking on a probe."""
    if not _snapshot_fresh():
        _kick_refresh()
    with _runtime_lock:
        snapshot = dict(_ps_cache or {})
        last_known = dict(_runtime_ctx_last)
    out: dict[str, int] = {}
    for raw in model_ids:
        mid = _normalize(raw) or raw
        if not _is_ollama(mid):
            try:
                from .catalog import read_limits

                out[raw] = int(read_limits(mid).get("context_length") or 0)
            except Exception:  # noqa: BLE001
                out[raw] = 0
            continue
        try:
            from .catalog import _explicit_context_override

            explicit = int(_explicit_context_override(mid) or 0)
        except Exception:  # noqa: BLE001
            explicit = 0
        if explicit > 0:
            out[raw] = explicit
            continue
        runtime = int(snapshot.get(_model_name(mid)) or 0)
        if runtime <= 0:
            runtime = int(last_known.get(mid) or 0)
        if runtime <= 0:
            runtime = int(getattr(settings, "ollama_num_ctx", 0) or 0)
        out[raw] = runtime
    return out


def sticky_num_ctx(model_id: str | None) -> int:
    return int(_sticky_num_ctx.get(_normalize(model_id)) or 0)


def _remember_num_ctx(mid: str, window: int) -> None:
    if window <= 0:
        return
    current = int(_sticky_num_ctx.get(mid) or 0)
    if window > current:
        _sticky_num_ctx[mid] = window
        _bump("num_ctx_expansions")


def _observe_window(mid: str, window: int) -> None:
    """Raise an existing sticky window when the server reports a larger one.

    Observation never starts pinning a window by itself: until an auto-expansion
    happened, local calls keep letting the Ollama server decide ``num_ctx``.
    """
    if window <= 0:
        return
    current = int(_sticky_num_ctx.get(mid) or 0)
    if current > 0 and window > current:
        _sticky_num_ctx[mid] = window


# ── Endpoint / transport ──────────────────────────────────────────────────────
_CACHE_KEY_PROVIDERS = frozenset({"openai", "azure", "openrouter", "opencode", "venice"})


def _has_image_block(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in (
                    "image_url",
                    "image",
                    "input_image",
                ):
                    return True
    return False


def _transport_model(endpoint: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    """Pick the litellm model id for this request.

    LiteLLM's ``deepseek/`` adapter drops ``image_url`` content. DeepSeek's
    OpenAI-compatible endpoint accepts images, so route image-bearing DeepSeek
    requests through ``openai/<model>`` with the same api_base/key. Text/tool
    turns keep the native ``deepseek/`` adapter (reasoning/cache behavior).
    """
    model = str(endpoint.get("litellm_model") or endpoint.get("model") or "")
    mid = str(endpoint.get("model") or "")
    provider = mid.split("/", 1)[0].lower()
    if (
        provider == "deepseek"
        and not endpoint.get("litellm_model")
        and _has_image_block(messages)
    ):
        bare = mid.split("/", 1)[1] if "/" in mid else mid
        return f"openai/{bare}"
    return model


def _cache_key_hints(endpoint: dict[str, Any]) -> dict[str, str]:
    session = request_session()
    if not session:
        return {}
    model_id = str(endpoint.get("model") or "")
    provider = model_id.split("/", 1)[0].lower()
    if provider not in _CACHE_KEY_PROVIDERS:
        return {}
    return {
        "prompt_cache_key": session,
        "x-session-affinity": session,
    }


def _merge_cache_hints(
    kwargs: dict[str, Any], endpoint: dict[str, Any]
) -> dict[str, Any]:
    hints = _cache_key_hints(endpoint)
    if not hints:
        return kwargs
    body = dict(kwargs.get("extra_body") or {})
    body.setdefault("prompt_cache_key", hints["prompt_cache_key"])
    kwargs["extra_body"] = body
    headers = dict(kwargs.get("extra_headers") or {})
    headers.setdefault("x-session-affinity", hints["x-session-affinity"])
    kwargs["extra_headers"] = headers
    return kwargs


def ensure_provider(model: str, api_base: str | None) -> str:
    if "/" in model:
        return model
    base = (api_base or "").lower()
    if "deepseek" in base:
        return f"deepseek/{model}"
    if "openai" in base:
        return f"openai/{model}"
    return model


def render_message(msg: Any) -> dict[str, Any]:
    """Render a BaseMessage / dict / tuple / str as an OpenAI message dict."""
    if isinstance(msg, str):
        return {"role": "user", "content": msg}
    if isinstance(msg, tuple) and len(msg) == 2:
        return {"role": str(msg[0]), "content": msg[1]}
    if isinstance(msg, dict):
        return {k: v for k, v in msg.items() if not str(k).startswith("_")}
    role = getattr(msg, "type", None)
    role_map = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
        "tool": "tool",
    }
    return {
        "role": role_map.get(role, "user"),
        "content": getattr(msg, "content", ""),
    }


def parse_response(resp: Any, requested_model: str) -> ChatResult:
    choice = resp.choices[0]
    message = choice.message
    raw_tool_calls = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for tc in raw_tool_calls:
        fn = getattr(tc, "function", None)
        raw_args = getattr(fn, "arguments", "") if fn else ""
        if isinstance(raw_args, (dict, list)):
            arguments = json.dumps(raw_args, ensure_ascii=False)
        elif raw_args is None:
            arguments = ""
        else:
            arguments = str(raw_args)
        calls.append(
            ToolCall(
                id=getattr(tc, "id", "") or "",
                name=getattr(fn, "name", "") if fn else "",
                arguments=arguments,
            )
        )
    usage: dict[str, Any] = {}
    if getattr(resp, "usage", None) is not None:
        u = resp.usage
        usage = {
            "input": getattr(u, "prompt_tokens", 0) or 0,
            "output": getattr(u, "completion_tokens", 0) or 0,
            "total": getattr(u, "total_tokens", 0) or 0,
        }
    return ChatResult(
        content=getattr(message, "content", "") or "",
        tool_calls=calls,
        finish_reason=_normalize_finish_reason(getattr(choice, "finish_reason", "stop")),
        usage=usage,
        model=getattr(resp, "model", requested_model) or requested_model,
    )


FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "max_tokens": "length",
    "tool_calls": "tool-calls",
    "function_call": "tool-calls",
    "content_filter": "content-filter",
    "error": "error",
}


def _normalize_finish_reason(finish_reason: str | None) -> str:
    if not finish_reason:
        return "stop"
    return FINISH_REASON_MAP.get(str(finish_reason).strip().lower(), "unknown")


@dataclass(frozen=True)
class RuntimeSpec:
    """Immutable inputs that identify one runtime resolution."""

    tier: str = "auto"
    temperature: float = DEFAULT_TEMPERATURE
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout: int = DEFAULT_TIMEOUT
    max_tokens: int | None = None
    model_override: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> str:
        return "|".join(
            [
                self.tier,
                f"{self.temperature}",
                str(self.max_retries),
                str(self.timeout),
                str(self.max_tokens),
                str(self.model_override or ""),
                repr(sorted(self.extra.items())),
                str(request_model() or ""),
            ]
        )


def _keep_alive_value() -> str | int | None:
    """Normalize keep_alive for Ollama.

    Pydantic string settings turn env ``OLLAMA_KEEP_ALIVE=-1`` into the string
    ``"-1"``; Ollama's Go duration parser rejects that ("missing unit"). Numeric
    strings must be sent as integers; durations such as ``"30m"`` stay strings.
    """
    raw = getattr(settings, "ollama_keep_alive", "")
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _is_local_ollama_endpoint(endpoint: dict[str, Any]) -> bool:
    mid = str(endpoint.get("model") or "")
    base = str(endpoint.get("api_base") or "").lower()
    return _is_ollama(mid) or "127.0.0.1" in base or "localhost" in base


class _CloudAdapter:
    """Cloud/OpenAI-compatible policy: no local window, output cap, or warmth."""

    name = "cloud"

    def is_local(self, endpoint: dict[str, Any]) -> bool:
        return False

    def timeout(self, endpoint: dict[str, Any], requested: int) -> int:
        return requested

    def max_tokens(
        self,
        endpoint: dict[str, Any],
        requested: int | None,
        messages: list[Any] | None,
        tools: list[dict] | None,
        window: int | None,
    ) -> int | None:
        return requested

    def num_ctx(self, endpoint: dict[str, Any], fit: FitResult) -> int:
        return 0

    def augment_kwargs(
        self, kwargs: dict[str, Any], endpoint: dict[str, Any]
    ) -> None:
        return None

    def supports_overflow_retry(self, endpoint: dict[str, Any]) -> bool:
        return False

    async def warm(self, endpoint: dict[str, Any]) -> bool:
        return False


class _OllamaAdapter(_CloudAdapter):
    """Local/self-hosted Ollama policy: window, output cap, keep_alive, warmth."""

    name = "ollama"

    def is_local(self, endpoint: dict[str, Any]) -> bool:
        return _is_local_ollama_endpoint(endpoint)

    def timeout(self, endpoint: dict[str, Any], requested: int) -> int:
        if requested != DEFAULT_TIMEOUT:
            return requested
        return LOCAL_OLLAMA_TIMEOUT

    def max_tokens(
        self,
        endpoint: dict[str, Any],
        requested: int | None,
        messages: list[Any] | None,
        tools: list[dict] | None,
        window: int | None,
    ) -> int | None:
        if requested is None:
            return None
        mid = str(endpoint.get("model") or "")
        if not _is_ollama(mid):
            return requested
        cap = min(requested, LOCAL_OLLAMA_MAX_TOKENS)
        try:
            effective = int(window or 0)
            if effective <= 0:
                effective = int(effective_window(mid, settings.ollama_num_ctx) or 0)
            if effective > 0 and messages:
                from cognition.context_meter import get_meter

                rendered = [render_message(m) for m in messages]
                est = get_meter().estimate(rendered, tools=tools, model_id=mid).total
                cap = min(cap, max(512, effective - est - 256))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ollama max_tokens estimate failed, using cap: %s", exc)
        return cap

    def num_ctx(self, endpoint: dict[str, Any], fit: FitResult) -> int:
        mid = str(endpoint.get("model") or "")
        if not _is_ollama(mid):
            return 0
        try:
            from .catalog import _explicit_context_override

            explicit = int(_explicit_context_override(mid) or 0)
        except Exception:  # noqa: BLE001
            explicit = 0
        if fit.num_ctx > 0:
            _remember_num_ctx(mid, fit.num_ctx)
            return fit.num_ctx
        if explicit > 0:
            return 0
        return sticky_num_ctx(mid)

    def augment_kwargs(
        self, kwargs: dict[str, Any], endpoint: dict[str, Any]
    ) -> None:
        # A local vLLM / LM Studio / LiteLLM proxy is also localhost but does not
        # understand Ollama's keep_alive; only real Ollama endpoints get it.
        if not (endpoint.get("is_ollama") or _is_ollama(str(endpoint.get("model") or ""))):
            return
        keep_alive = _keep_alive_value()
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive

    def supports_overflow_retry(self, endpoint: dict[str, Any]) -> bool:
        return _is_ollama(str(endpoint.get("model") or ""))

    async def warm(self, endpoint: dict[str, Any]) -> bool:
        mid = str(endpoint.get("model") or "")
        if not (endpoint.get("is_ollama") or _is_ollama(mid)):
            return False
        base = _ollama_native_base(endpoint.get("api_base")) or _ollama_base_url()
        # 自定义 Provider 的模型 id 前缀不一定是 ollama/ollama_chat（例如
        # `ollama-llocal/qwen`），端点已解析出真正的 litellm_model，用它取模型名。
        name = _model_name(str(endpoint.get("litellm_model") or mid))
        keep_alive = _keep_alive_value()
        payload: dict[str, Any] = {"model": name, "prompt": "", "stream": False}
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        try:
            await asyncio.to_thread(_warm_sync, base, payload)
            _bump("warm_ok")
            logger.info(
                "local model warm: model=%s keep_alive=%s",
                mid,
                keep_alive if keep_alive is not None else "-",
            )
            return True
        except Exception as exc:  # noqa: BLE001 - warmth must never fail a turn
            _bump("warm_failed")
            logger.warning("local model warm failed: model=%s error=%s", mid, exc)
            return False


_CLOUD_ADAPTER = _CloudAdapter()
_OLLAMA_ADAPTER = _OllamaAdapter()


def _adapter_for(endpoint: dict[str, Any]) -> _CloudAdapter:
    return _OLLAMA_ADAPTER if _is_local_ollama_endpoint(endpoint) else _CLOUD_ADAPTER


class ModelRuntime:
    """A resolved model execution context with a small call interface."""

    def __init__(self, spec: RuntimeSpec) -> None:
        self.spec = spec
        self.tier = spec.tier if spec.tier in TIER_MAP else "auto"
        self.temperature = spec.temperature
        self.max_retries = spec.max_retries
        self.timeout = spec.timeout
        self.max_tokens = (
            spec.max_tokens if spec.max_tokens is not None else settings.llm_max_tokens
        )
        self.model_override = (spec.model_override or "").strip() or None
        self.extra = spec.extra
        self._endpoint_cache: dict[str, Any] | None = None

    # ── resolution ──────────────────────────────────────────────────
    def _direct_model(self) -> str:
        if self.model_override:
            return self.model_override
        model = request_model()
        if model:
            return model
        import os

        env_key = f"TIER_{self.tier.upper()}_MODEL"
        model = os.environ.get(env_key)
        if model:
            return model
        return settings.llm_model

    def model_name(self) -> str:
        return str(self._endpoint().get("model", ""))

    def endpoint(self) -> dict[str, Any]:
        return dict(self._endpoint())

    def _endpoint(self) -> dict[str, Any]:
        if self._endpoint_cache is not None:
            return self._endpoint_cache
        proxy = settings.litellm_proxy_url
        if proxy:
            result: dict[str, Any] = {
                "model": self.model_override or TIER_MAP.get(self.tier, "smart-router"),
                "api_base": proxy,
                "api_key": settings.litellm_proxy_key or "sk-litellm-proxy",
                "is_ollama": False,
            }
            self._endpoint_cache = result
            return result
        mid = ensure_provider(self._direct_model(), settings.llm_api_base)
        try:
            from .catalog import provider_api

            creds = provider_api(mid)
        except Exception as exc:  # noqa: BLE001 - catalog must not break a call
            logger.warning("provider_api(%s) failed, using config fallback: %s", mid, exc)
            creds = {}
        if _is_ollama(mid) or creds.get("is_ollama"):
            result = {
                "model": mid,
                "litellm_model": creds.get("litellm_model"),
                "api_base": creds.get("api_base"),
                "api_key": creds.get("api_key") or "ollama",
                "options": creds.get("options"),
                "is_ollama": True,
            }
        else:
            result = {
                "model": mid,
                "litellm_model": creds.get("litellm_model"),
                "api_base": creds.get("api_base") or settings.llm_api_base,
                "api_key": creds.get("api_key") or settings.llm_api_key,
                "options": creds.get("options"),
                "is_ollama": bool(creds.get("is_ollama")),
            }
        self._endpoint_cache = result
        return result

    def _provider_kwargs(self, endpoint: dict[str, Any]) -> dict[str, Any]:
        try:
            from .catalog import litellm_extra_kwargs

            return litellm_extra_kwargs(endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.warning("litellm_extra_kwargs failed: %s", exc)
            return {}

    def _is_local_ollama(self, endpoint: dict[str, Any]) -> bool:
        return _adapter_for(endpoint).is_local(endpoint)

    def _timeout_for(self, endpoint: dict[str, Any]) -> int:
        return _adapter_for(endpoint).timeout(endpoint, self.timeout)

    def _max_tokens_for(
        self,
        endpoint: dict[str, Any],
        messages: list[Any] | None = None,
        tools: list[dict] | None = None,
        window: int | None = None,
    ) -> int | None:
        return _adapter_for(endpoint).max_tokens(
            endpoint, self.max_tokens, messages, tools, window
        )

    def _fit(
        self,
        endpoint: dict[str, Any],
        rendered: list[dict[str, Any]],
        tools: list[dict] | None,
    ) -> FitResult:
        mid = str(endpoint.get("model") or "")
        try:
            from . import context_guard as _guard

            return _guard.fit_messages(mid, rendered, tools)
        except Exception as exc:  # noqa: BLE001
            if is_overflow_error(exc):
                raise
            logger.warning("Ollama prompt fit failed for %s (passthrough): %s", mid, exc)
            return FitResult(model=mid, messages=rendered)

    def _prepare_call(
        self, messages: list[Any], tools: list[dict] | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int | None, FitResult]:
        endpoint = self._endpoint()
        rendered = [render_message(m) for m in messages]
        fit = self._fit(endpoint, rendered, tools)
        from .cache_hints import apply_cache_hints

        rendered = apply_cache_hints(fit.messages, str(endpoint.get("model") or ""))
        max_tokens = self._max_tokens_for(
            endpoint, rendered, tools, window=fit.window or None
        )
        if fit.window > 0:
            max_tokens = fit.reserve if max_tokens is None else min(max_tokens, fit.reserve)
        return endpoint, rendered, max_tokens, fit

    def _num_ctx_for(self, endpoint: dict[str, Any], fit: FitResult) -> int:
        return _adapter_for(endpoint).num_ctx(endpoint, fit)

    def _transport_kwargs(
        self, messages: list[Any], tools: list[dict] | None
    ) -> tuple[dict[str, Any], dict[str, Any], FitResult]:
        endpoint, rendered, max_tokens, fit = self._prepare_call(messages, tools)
        kwargs: dict[str, Any] = {
            "model": _transport_model(endpoint, rendered),
            "api_base": endpoint.get("api_base"),
            "api_key": endpoint.get("api_key"),
            "messages": rendered,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "timeout": self._timeout_for(endpoint),
            "num_retries": self.max_retries,
        }
        if tools:
            kwargs["tools"] = tools
        kwargs.update(self._provider_kwargs(endpoint))
        kwargs.update(self.extra)
        num_ctx = self._num_ctx_for(endpoint, fit)
        if num_ctx > 0:
            kwargs["num_ctx"] = num_ctx
        _adapter_for(endpoint).augment_kwargs(kwargs, endpoint)
        _merge_cache_hints(kwargs, endpoint)
        return kwargs, endpoint, fit

    @staticmethod
    def _usage_input(usage: Any) -> int:
        if usage is None:
            return 0
        if isinstance(usage, dict):
            return int(usage.get("input") or usage.get("prompt_tokens") or 0)
        return int(getattr(usage, "prompt_tokens", 0) or 0)

    def _record_usage(self, endpoint: dict[str, Any], fit: FitResult, usage: Any) -> None:
        if fit.window <= 0:
            return
        record_actual_usage(
            str(endpoint.get("model") or ""),
            fit.estimated_after,
            self._usage_input(usage),
        )

    def prepare_transport(
        self, messages: list[Any], tools: list[dict] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any], FitResult]:
        """Public seam used by ``TierModel`` compatibility wrappers."""
        return self._transport_kwargs(messages, tools)

    # ── transport ──────────────────────────────────────────────────
    def complete(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        call_kwargs, endpoint, fit = self._transport_kwargs(messages, tools)
        call_kwargs.update(kwargs)
        try:
            resp = litellm.completion(**call_kwargs)
        except Exception as exc:  # noqa: BLE001
            retry = self._apply_overflow_retry(messages, tools, kwargs, exc)
            if retry is None:
                raise
            call_kwargs, endpoint, fit = retry
            resp = litellm.completion(**call_kwargs)
        result = parse_response(resp, str(call_kwargs.get("model", "")))
        self._record_usage(endpoint, fit, result.usage)
        return result

    async def acomplete(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        call_kwargs, endpoint, fit = self._transport_kwargs(messages, tools)
        call_kwargs.update(kwargs)
        try:
            resp = await litellm.acompletion(**call_kwargs)
        except Exception as exc:  # noqa: BLE001
            retry = self._apply_overflow_retry(messages, tools, kwargs, exc)
            if retry is None:
                raise
            call_kwargs, endpoint, fit = retry
            resp = await litellm.acompletion(**call_kwargs)
        result = parse_response(resp, str(call_kwargs.get("model", "")))
        self._record_usage(endpoint, fit, result.usage)
        return result

    def _apply_overflow_retry(
        self,
        messages: list[Any],
        tools: list[dict] | None,
        extra: dict[str, Any],
        exc: BaseException,
    ) -> tuple[dict[str, Any], dict[str, Any], FitResult] | None:
        if getattr(exc, "preflight", False) or not is_overflow_error(exc):
            return None
        endpoint = self._endpoint()
        if not _adapter_for(endpoint).supports_overflow_retry(endpoint):
            return None
        rendered = [render_message(m) for m in messages]
        refit_after_overflow(str(endpoint.get("model") or ""), rendered, tools)
        call_kwargs, endpoint, fit = self._transport_kwargs(messages, tools)
        call_kwargs.update(extra)
        return call_kwargs, endpoint, fit

    async def _stream_once(
        self,
        messages: list[Any],
        tools: list[dict] | None,
        on_text: Any,
        disable_thinking: bool,
        extra: dict[str, Any],
    ) -> Any:
        from .stream import stream_chat

        endpoint, rendered, max_tokens, fit = self._prepare_call(messages, tools)
        extra_kwargs = {**self._provider_kwargs(endpoint), **self.extra, **extra}
        num_ctx = self._num_ctx_for(endpoint, fit)
        if num_ctx > 0:
            extra_kwargs["num_ctx"] = num_ctx
        _adapter_for(endpoint).augment_kwargs(extra_kwargs, endpoint)
        _merge_cache_hints(extra_kwargs, endpoint)
        result = await stream_chat(
            rendered,
            model=_transport_model(endpoint, rendered),
            tools=tools,
            api_key=endpoint.get("api_key"),
            api_base=endpoint.get("api_base"),
            extra=extra_kwargs,
            max_tokens=max_tokens,
            temperature=self.temperature,
            timeout=self._timeout_for(endpoint),
            num_retries=self.max_retries,
            disable_thinking=disable_thinking,
            on_text=on_text,
        )
        self._record_usage(endpoint, fit, result.usage)
        return result

    async def stream(
        self,
        messages: list[Any],
        *,
        tools: list[dict] | None = None,
        on_text: Any = None,
        disable_thinking: bool = False,
        **extra: Any,
    ) -> Any:
        try:
            return await self._stream_once(
                messages, tools, on_text, disable_thinking, extra
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "preflight", False) or not is_overflow_error(exc):
                raise
            endpoint = self._endpoint()
            if not _adapter_for(endpoint).supports_overflow_retry(endpoint):
                raise
            rendered = [render_message(m) for m in messages]
            refit_after_overflow(str(endpoint.get("model") or ""), rendered, tools)
            return await self._stream_once(
                messages, tools, on_text, disable_thinking, extra
            )

    # ── warmth ─────────────────────────────────────────────────────
    def is_local(self) -> bool:
        endpoint = self._endpoint()
        return _adapter_for(endpoint).is_local(endpoint)

    async def warm(self) -> bool:
        """Load a local Ollama model with the current keep_alive (best effort)."""
        endpoint = self._endpoint()
        return await _adapter_for(endpoint).warm(endpoint)


def _warm_sync(base: str, payload: dict[str, Any]) -> None:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "agentsuper/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


# ── Resolution / caching ──────────────────────────────────────────────────────
def resolve_runtime(spec: RuntimeSpec) -> ModelRuntime:
    """Return the turn-scoped runtime for ``spec`` (or a fresh one off-turn)."""
    scope = _scope_var.get()
    if scope is None:
        return ModelRuntime(spec)
    key = spec.cache_key()
    runtime = scope.get(key)
    if runtime is None:
        runtime = ModelRuntime(spec)
        scope[key] = runtime
    return runtime


def runtime_for_model(
    model_id: str | None, *, max_tokens: int | None = None, **kwargs: Any
) -> ModelRuntime:
    return resolve_runtime(
        RuntimeSpec(model_override=model_id, max_tokens=max_tokens, **kwargs)
    )


_warm_tasks: set[asyncio.Task] = set()


def schedule_warm(model_id: str | None) -> None:
    """Warm a local model in the background, if one is selected and running."""
    if not model_id:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        try:
            selected = runtime_for_model(model_id)
            if not selected.is_local():
                return
            await asyncio.to_thread(refresh_local_windows)
            await selected.warm()
        except Exception:  # noqa: BLE001
            logger.debug("scheduled warm failed for %s", model_id, exc_info=True)

    task = loop.create_task(_run())
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)


__all__ = [
    "COMPRESS_MODEL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "LOCAL_OLLAMA_MAX_TOKENS",
    "LOCAL_OLLAMA_TIMEOUT",
    "ModelRuntime",
    "RuntimeSpec",
    "TIER_MAP",
    "TIER_ORDER",
    "begin_scope",
    "current_scope",
    "effective_window",
    "end_scope",
    "ensure_provider",
    "forget_runtime_context",
    "probe_runtime_context_length",
    "refresh_local_windows",
    "render_message",
    "request_model",
    "request_session",
    "reset_request_model",
    "reset_request_session",
    "reset_scope",
    "reset_state",
    "resolve_runtime",
    "runtime_context_lengths",
    "runtime_for_model",
    "runtime_stats",
    "schedule_warm",
    "set_request_model",
    "set_request_session",
    "sticky_num_ctx",
]
