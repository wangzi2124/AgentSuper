"""流式调用内核（Stream Kernel）。

对齐 agent.md §2.2：所有 LLM 调用统一经 models 层。`router.TierModel` 负责 tier 路由，
本模块负责「一次调用」的完整机制（老 `RAGAgent._llm_call` 的业务部分）：

1. 传输：litellm 流式（`stream=True`），建连失败或流式在 finish_reason 前中断 → 回退非流式；
2. 累积：把 chunk 增量拼成完整 message（content / reasoning_content / tool_calls）；
3. 方言归一化：Ollama 把工具调用以 JSON 文本流入 content → 重组为标准 tool_calls，
   未知工具名则抽取 arguments 内嵌回复（见 `dialect.py`）；
4. reasoning 回退：content 为空、仅 reasoning_content 有输出时用 reasoning 兜底。

观测/计费（trace / monitor / chainlog / usage 累加）依赖 app 层，由调用方在适配器中完成，
本层不 import `app.*`。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import litellm

from .dialect import (
    TCC_INNER_KEYS,
    TCC_PATTERN,
    parse_streamed_tool_call,
    tcc_prefix_ok,
)


@dataclass
class StreamResult:
    content: str = ""
    tool_calls: list | None = None
    finish_reason: str | None = None
    usage: object | None = None
    content_from_reasoning: bool = False
    interrupted: bool = False
    stream_error: Exception | None = None


def _build_tool_calls(tool_slots: dict[int, dict]) -> list | None:
    if not tool_slots:
        return None
    return [
        SimpleNamespace(
            id=slot["id"],
            type=slot["type"],
            function=SimpleNamespace(name=slot["function"]["name"], arguments=slot["function"]["arguments"]),
        )
        for _, slot in sorted(tool_slots.items())
    ]


async def collect_stream(stream, tool_names: set[str] | None = None, on_text=None) -> StreamResult:
    """累积一次流式响应（等价移植老 `_llm_call` 的 chunk 循环 + 组装）。"""
    text_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    pending_text: list[str] = []
    tool_slots: dict[int, dict] = {}
    finish_reason = None
    usage = None
    stream_interrupted = False
    stream_error: Exception | None = None
    try:
        async for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = u
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            choice = choices[0]
            fr = getattr(choice, "finish_reason", None)
            if fr:
                finish_reason = fr
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            c = getattr(delta, "content", None)
            if c:
                text_chunks.append(c)
                pending_text.append(c)
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_chunks.append(rc)
                # 仍可能是 Ollama 工具调用 JSON 的文本 → 暂缓推送，避免闪给前端
                circum = "".join(text_chunks)
                if TCC_PATTERN.match(circum) or tcc_prefix_ok(circum) or circum.lstrip().startswith("{"):
                    if TCC_PATTERN.match(circum) or tcc_prefix_ok(circum):
                        continue
                    if circum.count("{") > circum.count("}"):
                        continue
                pending_str = "".join(pending_text)
                if pending_str.strip() in ("{}", "[]", ""):
                    pending_text.clear()
                    continue
                if on_text is not None:
                    on_text(pending_str)
                pending_text.clear()
            for tc in (getattr(delta, "tool_calls", None) or []):
                idx = getattr(tc, "index", None) or 0
                slot = tool_slots.setdefault(
                    idx,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["function"]["name"] += fn.name
                    if getattr(fn, "arguments", None):
                        slot["function"]["arguments"] += fn.arguments
    except Exception as e:  # noqa: BLE001
        stream_interrupted = True
        stream_error = e

    if stream_interrupted and finish_reason is None:
        return StreamResult(
            finish_reason=finish_reason, usage=usage,
            interrupted=True, stream_error=stream_error,
        )

    content = "".join(text_chunks)
    # [零进展] 正文是否由模型自己产出，必须在 reasoning 回退之前取
    content_is_real = bool(content.strip())
    if not content.strip() and reasoning_chunks:
        reasoning = "".join(reasoning_chunks).strip()
        if reasoning:
            content = reasoning

    # [Ollama 流式兼容] 工具调用被注入为 content 中的 JSON 文本 → 重组
    converted = False
    call = parse_streamed_tool_call(content)
    if call and not tool_slots:
        if tool_names and call["name"] in tool_names:
            tool_slots[0] = {
                "id": f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                },
            }
            content = ""
            converted = True
        else:
            for k in TCC_INNER_KEYS:
                v = call["arguments"].get(k)
                if isinstance(v, str) and v.strip():
                    content = v.strip()
                    converted = True
                    break

    if on_text is not None and pending_text and not converted:
        pending_str = "".join(pending_text)
        if pending_str.strip() not in ("{}", "[]"):
            on_text(pending_str)

    return StreamResult(
        content=content,
        tool_calls=_build_tool_calls(tool_slots),
        finish_reason=finish_reason,
        usage=usage,
        content_from_reasoning=not content_is_real,
    )


def nonstream_result(response, on_text=None) -> StreamResult:
    """把一次非流式响应转成 StreamResult（含 reasoning 回退与整段文本推送）。"""
    msg = getattr(response.choices[0], "message", None)
    raw_content = getattr(msg, "content", None) or ""
    content = raw_content
    if not content.strip():
        reasoning = getattr(msg, "reasoning_content", None) or ""
        if reasoning.strip():
            content = reasoning
    tool_calls = list(getattr(msg, "tool_calls", None) or []) or None
    if on_text is not None and content:
        on_text(content)
    return StreamResult(
        content=content,
        tool_calls=tool_calls,
        finish_reason=getattr(response.choices[0], "finish_reason", None),
        usage=getattr(response, "usage", None),
        content_from_reasoning=not bool(raw_content.strip()),
    )


async def stream_chat(
    messages: list,
    *,
    model: str,
    tools: list | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    extra: dict | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.1,
    timeout: int = 500,
    num_retries: int = 2,
    disable_thinking: bool = False,
    on_text=None,
) -> StreamResult:
    """发起一次模型调用（流式优先，建连失败/中断回退非流式），返回 StreamResult。

    失败时抛出底层异常，由调用方决定友好化与记账。

    **Ollama 工具轮次非流式（对齐 ollama.txt 规格）**：LiteLLM 对 Ollama 的流式
    工具调用存在已知缺陷（finish_reason 错误、缺少 tool call id、arguments 类型不匹配），
    导致工具调用在流式路径下丢失。规避策略：当请求带 tools 且模型为 Ollama 时，
    强制走非流式路径（invoke），非流式 transform_response 能正确设置 finish_reason
    并保留 tool_calls。最终自然语言回答（无 tools）仍可流式输出给用户。
    """
    extra = dict(extra or {})
    if disable_thinking and (model or "").lower().startswith("deepseek/"):
        eb = dict(extra.get("extra_body") or {})
        eb["thinking"] = {"type": "disabled"}
        extra["extra_body"] = eb
        extra.pop("thinking", None)

    base = dict(
        model=model,
        messages=messages,
        tools=tools,
        api_key=api_key,
        api_base=api_base,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        num_retries=num_retries,
        cache_prompt=True,
        **extra,
    )

    # [Ollama 工具轮次非流式] 规格：工具调用轮次用 invoke，避免流式 finish_reason/tool_call id 缺陷
    is_ollama = (model or "").lower().startswith(("ollama/", "ollama_chat/"))
    if tools and is_ollama:
        resp = await litellm.acompletion(**base)
        return nonstream_result(resp, on_text)

    try:
        stream = await litellm.acompletion(
            **base, stream=True, stream_options={"include_usage": True}
        )
    except Exception:  # noqa: BLE001
        resp = await litellm.acompletion(**base)
        return nonstream_result(resp, on_text)

    names = {t.get("function", {}).get("name") for t in (tools or [])} or None
    result = await collect_stream(stream, tool_names=names, on_text=on_text)
    if result.interrupted and result.finish_reason is None:
        resp = await litellm.acompletion(**base)
        return nonstream_result(resp, on_text)
    return result
