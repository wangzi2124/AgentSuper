"""模型路由：`get_model(tier)` → 统一 ChatModel 适配器。

设计（对齐 agent.md §2.2）：
- 业务代码只调用 `get_model()`，禁止直连厂商 SDK。
- 每个 Agent 角色绑定固定 tier，避免「所有 Agent 都用最强模型」。
- 生产：`LITELLM_PROXY_URL` 指向 LiteLLM Proxy，`model` 用 tier 别名交给 Proxy 路由。
- 开发：未设置 Proxy 时回退直连 litellm（`settings.llm_*`），并记录一次告警。

注意：本环境未安装 `langchain-litellm`（`ChatLiteLLM`），故 `TierModel` 直接以
litellm 为底座实现 `invoke` / `ainvoke`，返回带 `content` / `tool_calls` 的结果对象，
接口形状与 LangChain ChatModel 保持兼容，便于后续替换。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from typing import Any

import litellm

from config import settings

from .context_guard import (
    FitResult,
    fit_messages,
    is_overflow_error,
    record_actual_usage,
    refit_after_overflow,
)
from .types import ChatResult, ToolCall

logger = logging.getLogger(__name__)

litellm.drop_params = True

# 本回合请求选中的模型（前端模型选择器 / 「模型管理」页）。
# 由运行时按回合注入 contextvar（deps.PlatformContext.run/stream、compat._graph_events），
# 随 context 传播到图节点任务（与 graph/nodes/base.py 的 permission hook 同一机制）。
# 目的：让用户在「模型管理」里选的模型真正参与调用，而不是只写进会话元数据。
_request_model_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_model", default=None
)


def set_request_model(model: str | None) -> contextvars.Token:
    """注入本回合选中的模型 id（None 表示沿用默认），返回 token 供 reset。"""
    return _request_model_var.set(model or None)


def reset_request_model(token: contextvars.Token) -> None:
    _request_model_var.reset(token)


def request_model() -> str | None:
    """当前回合选中的模型 id（未注入时为 None）。"""
    return _request_model_var.get()


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
# 本地 Ollama CPU 推理慢（实测 ~7.5 tok/s），子 Agent 全量工具提示词 120s 必超时
# （2026-10-19 实锤：deepseek-r1:1.5b 超时 ×2 重试 → SSE error）；仅默认值时放宽，
# 调用方显式传 timeout 仍按其值。
LOCAL_OLLAMA_TIMEOUT = 300
# 本地 Ollama 输出上限收窄：CPU 上模型跑飞生成能烧半小时；常规答案几百 token
# 不受影响。仅按 model 前缀判定（本地代理 tier 别名不受影响），取 min 不放大。
LOCAL_OLLAMA_MAX_TOKENS = 4096
DEFAULT_MAX_RETRIES = 2

COMPRESS_MODEL = "compress-model"


def get_compress_model(max_tokens: int | None = 800, model: str | None = None):
    """压缩专用模型（agent-1 §1.3）——独立 model_name 隔离成本，不走 smart-router。

    代理模式（LITELLM_PROXY_URL）用别名 `compress-model` 由代理路由并计费；
    直连模式优先使用显式传入的 ``model``，其次 ``settings.compress_model``，
    都没有时回落 tier-simple。
    """
    chosen = (model or settings.compress_model or "").strip()
    proxy = settings.litellm_proxy_url
    if proxy:
        # 代理模式：显式压缩模型直接作为代理 model 名；未配置时保持 smart-router。
        return TierModel(tier="auto", max_tokens=max_tokens, model_override=chosen or None)
    if chosen:
        return TierModel(tier="auto", max_tokens=max_tokens, model_override=chosen)
    return get_model("simple", max_tokens=max_tokens)


# finish_reason 归一化（对齐 opencode FinishReason 六值）
FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "max_tokens": "length",          # Anthropic/Bedrock 原生名
    "tool_calls": "tool-calls",
    "function_call": "tool-calls",   # 旧式 OpenAI
    "content_filter": "content-filter",
    "error": "error",
}


def normalize_finish_reason(finish_reason: str | None) -> str:
    """把 LiteLLM/OpenAI 式 finish_reason 归一化为 opencode FinishReason 六值。

    None 视为正常结束（stop）；未知值归一化为 "unknown"（保守续跑一轮）。
    """
    if not finish_reason:
        return "stop"
    return FINISH_REASON_MAP.get(str(finish_reason).strip().lower(), "unknown")


def _ensure_provider(model: str, api_base: str | None) -> str:
    """无 provider 前缀时按 api_base 推断（对齐 app/agent/graph.py 的既有逻辑）。"""
    if "/" in model:
        return model
    base = (api_base or "").lower()
    if "deepseek" in base:
        return f"deepseek/{model}"
    if "openai" in base:
        return f"openai/{model}"
    return model


def _is_ollama_model(model: str) -> bool:
    """判断模型 ID 是否为 Ollama 模型。

    规范实现收敛在 `models/catalog.py:is_ollama_model`（避免两处前缀判断漂移）；
    此处惰性委派，规避 `router → catalog` 的模块级循环导入。
    """
    from .catalog import is_ollama_model

    return is_ollama_model(model)


# 公共别名：既有调用方（app/agent/graphmod/generate.py、models/__init__.py）使用无下划线名。
ensure_provider = _ensure_provider


def _render_message(msg: Any) -> dict[str, Any]:
    """把 BaseMessage / dict / (role, content) / str 渲染成 OpenAI 消息 dict。"""
    if isinstance(msg, str):
        return {"role": "user", "content": msg}
    if isinstance(msg, tuple) and len(msg) == 2:
        return {"role": str(msg[0]), "content": msg[1]}
    if isinstance(msg, dict):
        return msg
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


def _parse_response(resp: Any, requested_model: str) -> ChatResult:
    choice = resp.choices[0]
    message = choice.message
    raw_tool_calls = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for tc in raw_tool_calls:
        fn = getattr(tc, "function", None)
        raw_args = getattr(fn, "arguments", "") if fn else ""
        # 归一化：部分 provider（Ollama 边缘路径）把 arguments 给成 dict/list，
        # OpenAI 契约要求 str —— 下游 json.loads/回放进对话都会炸，这里统一成 str。
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
        finish_reason=normalize_finish_reason(getattr(choice, "finish_reason", "stop")),
        usage=usage,
        model=getattr(resp, "model", requested_model) or requested_model,
    )


class TierModel:
    """按 tier 路由的 ChatModel 适配器。"""

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

    def _direct_model(self) -> str:
        """直连模式下的模型 id：显式 override > 请求选中 > `TIER_{tier}_MODEL` > `settings.llm_model`。

        ``model_override`` 用于后台固定模型（如压缩模型）；请求选中（前端模型选择器）/
        `TIER_*_MODEL` 优先于 `config.json` 的 `llm_model`。无请求模型时（后台任务、
        子 Agent）回落到 tier 环境变量或全局默认。
        """
        if self.model_override:
            return self.model_override
        model = _request_model_var.get()
        if model:
            return model
        env_key = f"TIER_{self.tier.upper()}_MODEL"
        model = os.environ.get(env_key)
        if model:
            return model
        return settings.llm_model

    def model_name(self) -> str:
        """解析后的完整模型 id（含 provider 前缀），供成本估算按键匹配。"""
        return str(self._endpoint().get("model", ""))

    def _endpoint(self) -> dict[str, Any]:
        """解析本次调用的模型 / 端点 / 凭证（对齐 `models/catalog.provider_api`）。

        优先级：`LITELLM_PROXY_URL` 代理 > 模型目录 Provider 注册（模型管理页保存的
        `api_base`/`api_key`）> `config.json` 的 `LLM_API_BASE`/`LLM_API_KEY`。此前这里直取
        `config.json`，导致用户在「模型管理」里改的密钥/端点不参与聊天调用（只在连通性自检
        生效）——这就是「改了 key 聊天仍报旧 key 无效」的根因。
        **Ollama 例外**：`ollama/*` 模型只认前端注册的 Provider，`config.json` 不兜底
        （否则 config.json 的 DeepSeek 端点顶掉 localhost、占位 key "ollama" 被网关拒绝）。

        `options`（Ollama `num_ctx`/`think`、DeepSeek `thinking`）由
        `_kwargs`/`astream` 经 `litellm_extra_kwargs()` 展开，不直接透传给 litellm。
        """
        proxy = settings.litellm_proxy_url
        if proxy:
            return {
                "model": self.model_override or TIER_MAP.get(self.tier, "smart-router"),
                "api_base": proxy,
                "api_key": settings.litellm_proxy_key or "sk-litellm-proxy",
            }
        mid = ensure_provider(self._direct_model(), settings.llm_api_base)
        try:
            from .catalog import provider_api

            creds = provider_api(mid)
        except Exception as exc:  # noqa: BLE001 - 目录/DB 异常不得中断 LLM 调用
            logger.warning("provider_api(%s) 解析失败，回退 config.json 凭证：%s", mid, exc)
            creds = {}
        if _is_ollama_model(mid) or creds.get("is_ollama"):
            # Ollama 只认前端「模型管理」注册的 Provider，后端 config.json 不兜底：
            # 此前 `api_base=None or settings.llm_api_base` 被 config.json 的 DeepSeek 端点
            # 顶掉、key 落成占位 "ollama" → 请求打 DeepSeek 报 ****lama is invalid。
            # None → litellm 原生发现（OLLAMA_HOST 环境变量 → localhost:11434）。
            return {
                "model": mid,
                "litellm_model": creds.get("litellm_model"),
                "api_base": creds.get("api_base"),
                "api_key": creds.get("api_key") or "ollama",
                "options": creds.get("options"),
            }
        return {
            "model": mid,
            "litellm_model": creds.get("litellm_model"),
            "api_base": creds.get("api_base") or settings.llm_api_base,
            "api_key": creds.get("api_key") or settings.llm_api_key,
            "options": creds.get("options"),
        }

    def _provider_kwargs(self, endpoint: dict[str, Any]) -> dict[str, Any]:
        """把 `_endpoint()` 的 provider `options` 展开成 litellm 顶层 kwargs。"""
        try:
            from .catalog import litellm_extra_kwargs

            return litellm_extra_kwargs(endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.warning("litellm_extra_kwargs 展开失败：%s", exc)
            return {}

    def _is_local_ollama(self, endpoint: dict[str, Any]) -> bool:
        """endpoint 是否指向本地 Ollama（model 前缀 或 api_base 指向本机）。"""
        mid = str(endpoint.get("model") or "")
        base = str(endpoint.get("api_base") or "").lower()
        return _is_ollama_model(mid) or "127.0.0.1" in base or "localhost" in base

    def _timeout_for(self, endpoint: dict[str, Any]) -> int:
        """本地 Ollama 用更宽的超时；显式注入的自定义 timeout 优先。"""
        if self.timeout != DEFAULT_TIMEOUT:
            return self.timeout
        if self._is_local_ollama(endpoint):
            return LOCAL_OLLAMA_TIMEOUT
        return self.timeout

    def _max_tokens_for(
        self,
        endpoint: dict[str, Any],
        messages: list[Any] | None = None,
        tools: list[dict] | None = None,
        window: int | None = None,
    ) -> int | None:
        """本地 Ollama 收窄输出上限；支持 ollama/ 和 ollama_chat/ 前缀。

        [防截断] 不能只按固定上限：Ollama 的 num_ctx 是 prompt+输出共享窗口，
        输出给太多会把 prompt 挤到窗口外、由推理服务**静默截断**（历史看似丢失）。
        这里按实际运行窗口减去估算 prompt 动态收窄，保证一次调用装得下。
        """
        mt = self.max_tokens
        if mt is None:
            return None
        mid = str(endpoint.get("model") or "")
        if not _is_ollama_model(mid):
            return mt
        cap = min(mt, LOCAL_OLLAMA_MAX_TOKENS)
        try:
            from config import settings
            from models.catalog import effective_context_length

            effective = int(window or 0)
            if effective <= 0:
                effective = int(effective_context_length(mid, settings.ollama_num_ctx) or 0)
            if effective > 0 and messages:
                from cognition.token_counter import estimate_tokens_messages, estimate_tools

                rendered = [_render_message(m) for m in messages]
                est = int(estimate_tokens_messages(rendered)) + int(estimate_tools(tools))
                cap = min(cap, max(512, effective - est - 256))
        except Exception as exc:  # noqa: BLE001 - 估算失败退回固定上限
            logger.debug("ollama max_tokens 估算失败，用固定上限：%s", exc)
        return cap

    def _prepare_call(
        self, messages: list[Any], tools: list[dict] | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int | None, FitResult]:
        """Resolve endpoint, fit the prompt, and compute the final output cap."""
        endpoint = self._endpoint()
        rendered = [_render_message(m) for m in messages]
        fit = self._fit(endpoint, rendered, tools)
        rendered = fit.messages
        max_tokens = self._max_tokens_for(
            endpoint, rendered, tools, window=fit.window or None
        )
        if fit.window > 0:
            max_tokens = fit.reserve if max_tokens is None else min(max_tokens, fit.reserve)
        return endpoint, rendered, max_tokens, fit

    def _fit(
        self,
        endpoint: dict[str, Any],
        rendered: list[dict[str, Any]],
        tools: list[dict] | None,
    ) -> FitResult:
        mid = str(endpoint.get("model") or "")
        try:
            return fit_messages(mid, rendered, tools)
        except Exception as exc:  # noqa: BLE001 - overflow must propagate
            if is_overflow_error(exc):
                raise
            logger.warning("Ollama prompt fit failed for %s (passthrough): %s", mid, exc)
            return FitResult(model=mid, messages=rendered)

    def _transport_kwargs(
        self, messages: list[Any], tools: list[dict] | None
    ) -> tuple[dict[str, Any], dict[str, Any], FitResult]:
        """纯传输 kwargs：消息渲染 + Ollama prompt 适配。"""
        endpoint, rendered, max_tokens, fit = self._prepare_call(messages, tools)
        kwargs: dict[str, Any] = {
            "model": endpoint.get("litellm_model") or endpoint["model"],
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
        if fit.num_ctx > 0:
            kwargs["num_ctx"] = fit.num_ctx
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

    def _apply_overflow_retry(
        self,
        messages: list[Any],
        tools: list[dict] | None,
        extra: dict[str, Any],
        exc: BaseException,
    ) -> tuple[dict[str, Any], dict[str, Any], FitResult] | None:
        """Invalidate the cached window and rebuild kwargs for one retry.

        Returns ``None`` when the error is not an Ollama context overflow.
        """
        if getattr(exc, "preflight", False) or not is_overflow_error(exc):
            return None
        endpoint = self._endpoint()
        if not _is_ollama_model(str(endpoint.get("model") or "")):
            return None
        rendered = [_render_message(m) for m in messages]
        refit_after_overflow(str(endpoint.get("model") or ""), rendered, tools)
        call_kwargs, endpoint, fit = self._transport_kwargs(messages, tools)
        call_kwargs.update(extra)
        return call_kwargs, endpoint, fit

    async def raw_ainvoke(
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
        result = _parse_response(resp, str(call_kwargs.get("model", "")))
        self._record_usage(endpoint, fit, result.usage)
        return result

    def raw_invoke(
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
        result = _parse_response(resp, str(call_kwargs.get("model", "")))
        self._record_usage(endpoint, fit, result.usage)
        return result

    async def _stream_once(
        self,
        messages: list[Any],
        tools: list[dict] | None,
        on_text,
        disable_thinking: bool,
        extra: dict[str, Any],
    ):
        from .stream import stream_chat

        endpoint, rendered, max_tokens, fit = self._prepare_call(messages, tools)
        extra_kwargs = {**self._provider_kwargs(endpoint), **self.extra, **extra}
        if fit.num_ctx > 0:
            extra_kwargs["num_ctx"] = fit.num_ctx
        result = await stream_chat(
            rendered,
            model=endpoint.get("litellm_model") or endpoint["model"],
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

    async def raw_stream(
        self,
        messages: list[Any],
        *,
        tools: list[dict] | None = None,
        on_text=None,
        disable_thinking: bool = False,
        **extra: Any,
    ):
        try:
            return await self._stream_once(
                messages, tools, on_text, disable_thinking, extra
            )
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "preflight", False) or not is_overflow_error(exc):
                raise
            endpoint = self._endpoint()
            if not _is_ollama_model(str(endpoint.get("model") or "")):
                raise
            rendered = [_render_message(m) for m in messages]
            refit_after_overflow(str(endpoint.get("model") or ""), rendered, tools)
            return await self._stream_once(
                messages, tools, on_text, disable_thinking, extra
            )

    async def ainvoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        """传输入口：模型差异由 Harness 的 ToolAdapter 负责。"""
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
        """流式传输（流式优先，失败/中断回退非流式），返回 `stream.StreamResult`。"""
        return await self.raw_stream(
            messages,
            tools=tools,
            on_text=on_text,
            disable_thinking=disable_thinking,
            **extra,
        )


def get_model(tier: str = "auto", **kwargs: Any) -> TierModel:
    """获取指定层级的模型实例（agent.md §2.2）。"""
    return TierModel(tier=tier, **kwargs)


def resolve_model(state: dict[str, Any] | None = None, default_tier: str = "medium") -> TierModel:
    """节点统一入口：优先用 State.model_binding，否则回落到默认 tier"""
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
    """向量化（对齐 agent.md RAG 管线使用的 `embedding` tier）。"""
    target = model or TIER_MAP["embedding"]
    resp = litellm.embedding(model=target, input=texts)
    return [d["embedding"] for d in resp.data]
