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

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import litellm

from config import settings

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
DEFAULT_MAX_RETRIES = 2

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


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


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
        calls.append(
            ToolCall(
                id=getattr(tc, "id", "") or "",
                name=getattr(fn, "name", "") if fn else "",
                arguments=getattr(fn, "arguments", "") if fn else "",
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
        **extra: Any,
    ) -> None:
        self.tier = tier if tier in TIER_MAP else "auto"
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens
        self.extra = extra

    def _direct_model(self) -> str:
        env_key = f"TIER_{self.tier.upper()}_MODEL"
        model = os.environ.get(env_key)
        if model:
            return model
        return settings.llm_model

    def model_name(self) -> str:
        """解析后的完整模型 id（含 provider 前缀），供成本估算按键匹配。"""
        return str(self._endpoint().get("model", ""))

    def _endpoint(self) -> dict[str, Any]:
        proxy = os.environ.get("LITELLM_PROXY_URL")
        if proxy:
            return {
                "model": TIER_MAP.get(self.tier, "smart-router"),
                "api_base": proxy,
                "api_key": os.environ.get("LITELLM_PROXY_KEY", "sk-litellm-proxy"),
            }
        api_base = settings.llm_api_base
        return {
            "model": ensure_provider(self._direct_model(), api_base),
            "api_base": api_base,
            "api_key": settings.llm_api_key,
        }

    def _kwargs(
        self, messages: list[Any], tools: list[dict] | None
    ) -> dict[str, Any]:
        endpoint = self._endpoint()
        kwargs: dict[str, Any] = {
            **endpoint,
            "messages": [_render_message(m) for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "num_retries": self.max_retries,
        }
        if tools:
            kwargs["tools"] = tools
        kwargs.update(self.extra)
        return kwargs

    async def ainvoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        call_kwargs = self._kwargs(messages, tools)
        call_kwargs.update(kwargs)
        resp = await litellm.acompletion(**call_kwargs)
        return _parse_response(resp, str(call_kwargs.get("model", "")))

    def invoke(
        self, messages: list[Any], *, tools: list[dict] | None = None, **kwargs: Any
    ) -> ChatResult:
        call_kwargs = self._kwargs(messages, tools)
        call_kwargs.update(kwargs)
        resp = litellm.completion(**call_kwargs)
        return _parse_response(resp, str(call_kwargs.get("model", "")))

    async def astream(
        self,
        messages: list[Any],
        *,
        tools: list[dict] | None = None,
        on_text=None,
        disable_thinking: bool = False,
        **extra: Any,
    ):
        """流式调用（流式优先，失败/中断回退非流式），返回 `stream.StreamResult`。

        这是 RAGAgent 等业务层唯一应使用的调用入口：tier 路由 + 传输 + 方言归一化
        全部收敛在此。观测/计费由调用方基于返回值完成。
        """
        from .stream import stream_chat

        endpoint = self._endpoint()
        return await stream_chat(
            [_render_message(m) for m in messages],
            model=endpoint["model"],
            tools=tools,
            api_key=endpoint.get("api_key"),
            api_base=endpoint.get("api_base"),
            extra={**self.extra, **extra},
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            timeout=self.timeout,
            num_retries=self.max_retries,
            disable_thinking=disable_thinking,
            on_text=on_text,
        )


def get_model(tier: str = "auto", **kwargs: Any) -> TierModel:
    """获取指定层级的模型实例（agent.md §2.2）。"""
    return TierModel(tier=tier, **kwargs)


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """向量化（对齐 agent.md RAG 管线使用的 `embedding` tier）。"""
    target = model or TIER_MAP["embedding"]
    resp = litellm.embedding(model=target, input=texts)
    return [d["embedding"] for d in resp.data]
