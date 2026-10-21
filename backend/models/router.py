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
from dataclasses import dataclass, field
from typing import Any

import litellm

from config import settings

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


def get_compress_model(max_tokens: int | None = 800):
    """压缩专用模型（agent-1 §1.3）——独立 model_name 隔离成本，不走 smart-router。

    代理模式（LITELLM_PROXY_URL）用别名 `compress-model` 由代理路由并计费；
    直连模式回落 tier-simple（与既有行为一致），并可用 COMPRESS_MODEL 环境变量覆盖。
    """
    proxy = os.environ.get("LITELLM_PROXY_URL")
    if proxy:
        return TierModel(tier="auto", max_tokens=max_tokens)
    env = os.environ.get("COMPRESS_MODEL")
    if env:
        return TierModel(tier="auto", max_tokens=max_tokens)
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
        **extra: Any,
    ) -> None:
        self.tier = tier if tier in TIER_MAP else "auto"
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_tokens = max_tokens if max_tokens is not None else settings.llm_max_tokens
        self.extra = extra

    def _direct_model(self) -> str:
        """直连模式下的模型 id：请求选中 > `TIER_{tier}_MODEL` > `settings.llm_model`。

        请求选中（前端模型选择器）/ `TIER_*_MODEL` 优先于 `.env` 的 `LLM_MODEL`，
        使用户在「模型管理」里选的模型真正生效；无请求模型时（后台任务、子 Agent）
        回落到 tier 环境变量或全局默认。
        """
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
        `api_base`/`api_key`）> `.env` 的 `LLM_API_BASE`/`LLM_API_KEY`。此前这里直取
        `.env`，导致用户在「模型管理」里改的密钥/端点不参与聊天调用（只在连通性自检
        生效）——这就是「改了 key 聊天仍报旧 key 无效」的根因。
        **Ollama 例外**：`ollama/*` 模型只认前端注册的 Provider，`.env` 不兜底
        （否则 .env 的 DeepSeek 端点顶掉 localhost、占位 key "ollama" 被网关拒绝）。

        `options`（Ollama `num_ctx`/`think`、DeepSeek `thinking`）由
        `_kwargs`/`astream` 经 `litellm_extra_kwargs()` 展开，不直接透传给 litellm。
        """
        proxy = os.environ.get("LITELLM_PROXY_URL")
        if proxy:
            return {
                "model": TIER_MAP.get(self.tier, "smart-router"),
                "api_base": proxy,
                "api_key": os.environ.get("LITELLM_PROXY_KEY", "sk-litellm-proxy"),
            }
        mid = ensure_provider(self._direct_model(), settings.llm_api_base)
        try:
            from .catalog import provider_api

            creds = provider_api(mid)
        except Exception as exc:  # noqa: BLE001 - 目录/DB 异常不得中断 LLM 调用
            logger.warning("provider_api(%s) 解析失败，回退 .env 凭证：%s", mid, exc)
            creds = {}
        if _is_ollama_model(mid) or creds.get("is_ollama"):
            # Ollama 只认前端「模型管理」注册的 Provider，后端 .env 不兜底：
            # 此前 `api_base=None or settings.llm_api_base` 被 .env 的 DeepSeek 端点
            # 顶掉、key 落成占位 "ollama" → 请求打 DeepSeek 报 ****lama is invalid。
            # None → litellm 原生发现（OLLAMA_HOST 环境变量 → localhost:11434）。
            return {
                "model": mid,
                "api_base": creds.get("api_base"),
                "api_key": creds.get("api_key") or "ollama",
                "options": creds.get("options"),
            }
        return {
            "model": mid,
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

    def _max_tokens_for(self, endpoint: dict[str, Any]) -> int | None:
        """本地 Ollama 收窄输出上限（CPU 跑飞生成保险）；支持 ollama/ 和 ollama_chat/ 前缀。"""
        mt = self.max_tokens
        if mt is None:
            return None
        mid = str(endpoint.get("model") or "")
        if _is_ollama_model(mid):
            return min(mt, LOCAL_OLLAMA_MAX_TOKENS)
        return mt

    def _kwargs(
        self, messages: list[Any], tools: list[dict] | None
    ) -> dict[str, Any]:
        endpoint = self._endpoint()
        kwargs: dict[str, Any] = {
            "model": endpoint["model"],
            "api_base": endpoint.get("api_base"),
            "api_key": endpoint.get("api_key"),
            "messages": [_render_message(m) for m in messages],
            "temperature": self.temperature,
            "max_tokens": self._max_tokens_for(endpoint),
            "timeout": self._timeout_for(endpoint),
            "num_retries": self.max_retries,
        }
        if tools:
            kwargs["tools"] = tools
        kwargs.update(self._provider_kwargs(endpoint))
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
        extra_kwargs = {**self._provider_kwargs(endpoint), **self.extra, **extra}
        return await stream_chat(
            [_render_message(m) for m in messages],
            model=endpoint["model"],
            tools=tools,
            api_key=endpoint.get("api_key"),
            api_base=endpoint.get("api_base"),
            extra=extra_kwargs,
            max_tokens=self._max_tokens_for(endpoint),
            temperature=self.temperature,
            timeout=self._timeout_for(endpoint),
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
