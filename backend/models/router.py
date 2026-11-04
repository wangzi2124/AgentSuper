"""模型路由 facade：`get_model(tier)` → 统一 ChatModel 适配器。

业务代码只调用 `get_model()`，禁止直连厂商 SDK。所有解析与传输行为已经
收敛到 `models/runtime.py` 的 `ModelRuntime`；本模块只保留稳定的 facade，
避免图构建、测试与压缩/切换调用点迁移。设计见
`docs/adr/0001-model-runtime-ownership.md`。
"""

from __future__ import annotations

from typing import Any

import litellm

from config import settings

from .context_guard import fit_messages  # noqa: F401 - re-export for callers/tests
from .runtime import (
    COMPRESS_MODEL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    FINISH_REASON_MAP,
    LOCAL_OLLAMA_MAX_TOKENS,
    LOCAL_OLLAMA_TIMEOUT,
    TIER_MAP,
    TIER_ORDER,
    ModelRuntime,
    RuntimeSpec,
    ensure_provider as _ensure_provider,
    parse_response as _parse_response,
    render_message as _render_message,
    request_model,
    reset_request_model,
    resolve_runtime,
    set_request_model,
)
from .types import ChatResult, ToolCall

__all__ = [
    "COMPRESS_MODEL",
    "ChatResult",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "FINISH_REASON_MAP",
    "LOCAL_OLLAMA_MAX_TOKENS",
    "LOCAL_OLLAMA_TIMEOUT",
    "TIER_MAP",
    "TIER_ORDER",
    "TierModel",
    "ToolCall",
    "_ensure_provider",
    "_is_ollama_model",
    "_parse_response",
    "_render_message",
    "embed",
    "ensure_provider",
    "get_compress_model",
    "get_model",
    "normalize_finish_reason",
    "request_model",
    "reset_request_model",
    "resolve_model",
    "set_request_model",
]

ensure_provider = _ensure_provider


def _is_ollama_model(model: str) -> bool:
    from .catalog import is_ollama_model

    return is_ollama_model(model)


def normalize_finish_reason(finish_reason: str | None) -> str:
    """把 LiteLLM/OpenAI 式 finish_reason 归一化为 opencode FinishReason 六值。"""
    from .runtime import _normalize_finish_reason

    return _normalize_finish_reason(finish_reason)


def get_compress_model(max_tokens: int | None = 800, model: str | None = None):
    chosen = (model or settings.compress_model or "").strip()
    proxy = settings.litellm_proxy_url
    if proxy:
        return TierModel(tier="auto", max_tokens=max_tokens, model_override=chosen or None)
    if chosen:
        return TierModel(tier="auto", max_tokens=max_tokens, model_override=chosen)
    return get_model("simple", max_tokens=max_tokens)


class TierModel:
    """按 tier 路由的 ChatModel facade；实际行为在 `ModelRuntime`。"""

    def __init__(
        self,
        tier: str = "auto",
        *,
        temperature: float = DEFAULT_TEMPERATURE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int | None = None,
        model_override: str | None = None,
        **extra: Any,
    ) -> None:
        self.tier = tier if tier in TIER_MAP else "auto"
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens
        self.model_override = (model_override or "").strip() or None
        self.extra = extra

    def _spec(self) -> RuntimeSpec:
        return RuntimeSpec(
            tier=self.tier,
            temperature=self.temperature,
            max_retries=self.max_retries,
            timeout=self.timeout,
            max_tokens=self.max_tokens,
            model_override=self.model_override,
            extra=dict(self.extra),
        )

    def _runtime(self) -> ModelRuntime:
        runtime = resolve_runtime(self._spec())
        # Tests (and emergency overrides) may replace ``_endpoint`` on the
        # facade; keep that seam observable without leaking it into the runtime.
        override = self.__dict__.get("_endpoint")
        if callable(override):
            runtime._endpoint_cache = override()
        return runtime

    def _direct_model(self) -> str:
        return self._runtime()._direct_model()

    def model_name(self) -> str:
        return self._runtime().model_name()

    def _endpoint(self) -> dict[str, Any]:
        return self._runtime().endpoint()

    def _provider_kwargs(self, endpoint: dict[str, Any]) -> dict[str, Any]:
        return self._runtime()._provider_kwargs(endpoint)

    def _is_local_ollama(self, endpoint: dict[str, Any]) -> bool:
        return self._runtime()._is_local_ollama(endpoint)

    def _timeout_for(self, endpoint: dict[str, Any]) -> int:
        return self._runtime()._timeout_for(endpoint)

    def _max_tokens_for(
        self,
        endpoint: dict[str, Any],
        messages: list[Any] | None = None,
        tools: list[dict] | None = None,
        window: int | None = None,
    ) -> int | None:
        return self._runtime()._max_tokens_for(endpoint, messages, tools, window)

    def _prepare_call(
        self, messages: list[Any], tools: list[dict] | None
    ):
        return self._runtime()._prepare_call(messages, tools)

    def _fit(self, endpoint, rendered, tools):
        return self._runtime()._fit(endpoint, rendered, tools)

    def _transport_kwargs(self, messages: list[Any], tools: list[dict] | None):
        return self._runtime()._transport_kwargs(messages, tools)

    def _apply_overflow_retry(self, messages, tools, extra, exc):
        return self._runtime()._apply_overflow_retry(messages, tools, extra, exc)

    async def raw_ainvoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        return await self._runtime().acomplete(messages, tools=tools, **kwargs)

    def raw_invoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        return self._runtime().complete(messages, tools=tools, **kwargs)

    async def raw_stream(
        self,
        messages: list[Any],
        *,
        tools: list[dict] | None = None,
        on_text=None,
        disable_thinking: bool = False,
        **extra: Any,
    ):
        return await self._runtime().stream(
            messages,
            tools=tools,
            on_text=on_text,
            disable_thinking=disable_thinking,
            **extra,
        )

    async def ainvoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        return await self.raw_ainvoke(messages, tools=tools, **kwargs)

    def invoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        return self.raw_invoke(messages, tools=tools, **kwargs)

    async def astream(
        self,
        messages: list[Any],
        *,
        tools: list[dict] | None = None,
        on_text=None,
        disable_thinking: bool = False,
        **extra: Any,
    ):
        return await self.raw_stream(
            messages,
            tools=tools,
            on_text=on_text,
            disable_thinking=disable_thinking,
            **extra,
        )


def get_model(tier: str = "auto", **kwargs: Any) -> TierModel:
    return TierModel(tier=tier, **kwargs)


def resolve_model(state: dict[str, Any] | None = None, default_tier: str = "medium") -> TierModel:
    if state and isinstance(state.get("model_binding"), dict):
        binding = state["model_binding"]
        tier = binding.get("tier") or default_tier
        proxy = settings.litellm_proxy_url
        if proxy:
            return TierModel(tier="auto")
        if tier in TIER_MAP or tier in ("auto",):
            return TierModel(tier=tier)
        return TierModel(tier=default_tier)
    return get_model(default_tier)


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    target = model or TIER_MAP["embedding"]
    resp = litellm.embedding(model=target, input=texts)
    return [d["embedding"] for d in resp.data]
