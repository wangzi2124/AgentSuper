"""节点公共设施：工具 schema 生成 + 工具调用循环。"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import re
import typing
from typing import Any, Awaitable, Callable

from execution.tool_registry import ToolDefinition, ToolRegistry
from models.router import TierModel
from observability.metrics import record_llm_call
from observability.tracing import trace_span
from utils.json_repair import (
    is_tool_call_markup,
    parse_answer_envelope,
    parse_tool_args,
)

from ..usage import empty_usage, merge_usage, usage_from_result

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

# 纠正文：小模型常把「工具调用」当纯文本正文输出（无原生 tool_calls），
# 循环误判为最终答案 → 前端看到裸 JSON（如 {"id":"call_x","type":"function",...}）。
_NUDGE_NOT_A_TOOL_CALL = (
    "上一条输出是工具调用 JSON，不是给用户的回答。"
    "请直接用纯文本给出最终答案：不要输出 JSON，不要再调用工具。"
)
_FINAL_ROUND_NUDGE = "工具调用轮数已达上限。请立即停止调用工具，直接用纯文本给出最终答案。"


def _json_object_candidate(text: str) -> dict[str, Any] | None:
    """正文中的 JSON 对象候选（只做形态提取，不做宽容修复）。

    归一化覆盖三种呈现：裸 JSON、markdown 代码围栏包裹、简短散文包裹
    （取第一个 `{` 到最后一个 `}` 的跨度）。解析失败返回 None，不打日志
    —— 对每个最终答案都会调用，避免日志噪音。
    """
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 8000 or "{" not in s:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    if not (s.startswith("{") and s.endswith("}")):
        a, b = s.find("{"), s.rfind("}")
        if not (0 <= a < b):
            return None
        s = s[a : b + 1]
    if not (s.startswith("{") and s.endswith("}") and len(s) <= 8000):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _looks_like_tool_call_json(text: str) -> bool:
    """正文是否实为「工具调用」而非答案文本（覆盖所有已观测模型形态）。"""
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 8000:
        return False
    # Hermes / DeepSeek DSML 等工具标记（utils.json_repair.is_tool_call_markup）
    if is_tool_call_markup(s):
        return True
    obj = _json_object_candidate(s)
    if obj is None:
        return False
    fn = obj.get("function")
    if isinstance(fn, dict):
        # 非标准形状（小模型自造）：{"function": {"function_name": "...", ...}}
        if isinstance(fn.get("function_name"), str):
            return True
        # 标准形状：{"id": ..., "type": "function", "function": {"name": ...}}
        if isinstance(fn.get("name"), str) and (
            obj.get("type") == "function" or "id" in obj
        ):
            return True
    calls = obj.get("tool_calls")
    if isinstance(calls, list) and calls:
        return True
    # 裸形状：{"name": "tool_x", "arguments": {...} | "{...}"}（无包装层）
    if isinstance(obj.get("name"), str) and "arguments" in obj:
        return True
    return False

# 高危工具审批钩子（§5.3 require_approval / §9 execution/approval.py）。
# 由 API 层（app/api/compat.py）在本回合内注入：`async (tool, args) -> bool`。
# 未注入时按「无审批」处理（默认工具 require_approval=False，行为不变）。
PermissionHook = Callable[[str, dict[str, Any]], Awaitable[bool]]
_permission_hook_var: contextvars.ContextVar[PermissionHook | None] = contextvars.ContextVar(
    "tool_permission_hook", default=None
)


def set_permission_hook(hook: PermissionHook | None) -> contextvars.Token:
    """注入本回合的工具审批钩子（随 contextvar 传播到图节点任务）。"""
    return _permission_hook_var.set(hook)


def reset_permission_hook(token: contextvars.Token) -> None:
    _permission_hook_var.reset(token)


_TEXT_BLOCK_TYPES = ("text", "input_text")
_IMAGE_BLOCK_TYPES = ("image_url", "image", "input_image")


def content_text(content: Any) -> str:
    """把消息 content 压成纯文本（分块列表 → 拼接 text 块，图片块 → `[图片]`）。

    多模态 content 是分块数组（image_url 块含 base64）。凡需要「纯文本视图」的地方
    （路由判据 / 规划取词 / 历史回显）都必须走这里 —— `str(content)` 会把 base64
    整个灌进提示词或接口响应。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in _IMAGE_BLOCK_TYPES:
                    parts.append("[图片]")
                elif block.get("type") in _TEXT_BLOCK_TYPES or "text" in block:
                    parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def has_image_content(content: Any) -> bool:
    """content 是否为含图片分块的多模态列表。"""
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") in _IMAGE_BLOCK_TYPES for b in content
        )
    return False


def _type_to_schema(tp: Any) -> dict[str, Any]:
    origin = typing.get_origin(tp)
    if origin is typing.Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return _type_to_schema(args[0])
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def json_schema_for(fn: Any) -> dict[str, Any]:
    """从函数签名推导 JSON schema（扁平化，必填最少化，§7.6.4）。"""
    try:
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001
        return {"type": "object", "properties": {}}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = _type_to_schema(hints.get(name, str))
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def build_tool_defs(
    registry: ToolRegistry, scope: str | None = None
) -> list[dict[str, Any]]:
    defs: list[dict[str, Any]] = []
    for td in registry.definitions(scope):
        params = td.parameters or json_schema_for(td.fn)
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": params,
                },
            }
        )
    return defs


def _describe(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(result)


async def _dispatch(
    td: ToolDefinition, args: dict[str, Any]
) -> str:
    try:
        result = td.fn(**args)
        if inspect.isawaitable(result):
            result = await result
        return _describe(result)
    except TypeError as e:
        return f"错误：参数不匹配（{e}）"
    except Exception as e:  # noqa: BLE001
        return f"错误：{e or repr(e)}"


async def run_tool_loop(
    model: TierModel,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    *,
    scope: str | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> dict[str, Any]:
    """LLM 工具调用循环，返回 {text, rounds, tool_trace, usage}。"""
    tool_defs = build_tool_defs(registry, scope)
    convo = list(messages)
    rounds = 0
    usage_total = empty_usage()
    tool_trace: list[dict[str, Any]] = []

    while rounds < max_rounds:
        with trace_span("llm.call", rounds=rounds):
            result = await model.ainvoke(convo, tools=tool_defs or None)
        rounds += 1
        label = model.model_name() if hasattr(model, "model_name") else model.tier
        usage_total = merge_usage(usage_total, usage_from_result(result, label))
        record_llm_call(
            result.model or model.tier,
            result.usage.get("input", 0),
            result.usage.get("output", 0),
        )

        if not result.tool_calls:
            text = result.content or ""
            if _looks_like_tool_call_json(text):
                if rounds < max_rounds:
                    # 工具调用被模型写成了正文（无原生 tool_calls）→ 纠正一轮，不当答案返回。
                    # 记录原始响应摘录做诊断（等价于 set_verbose 抓包，但不打印整包请求/密钥）。
                    logger.warning(
                        "模型把工具调用写成了正文（model=%s round=%d）: %.300s",
                        label,
                        rounds,
                        text,
                    )
                    convo.append({"role": "assistant", "content": text})
                    convo.append({"role": "user", "content": _NUDGE_NOT_A_TOOL_CALL})
                    continue
                break  # 轮数已尽：交由下方纯文本收尾，不把裸 JSON 当最终答案
            # 弱模型把回答包进 JSON 外壳（{"response": "..."}）→ 解包归一化
            envelope = parse_answer_envelope(text)
            return {
                "text": envelope or text,
                "rounds": rounds,
                "tool_trace": tool_trace,
                "usage": usage_total,
            }

        convo.append(
            {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in result.tool_calls
                ],
            }
        )
        for tc in result.tool_calls:
            raw_args = tc.arguments
            if isinstance(raw_args, dict):
                args: dict[str, Any] = raw_args
            else:
                # 宽容解析：弱模型常见坏 JSON（单引号/尾逗号/未转义换行/围栏）
                # —— utils.json_repair.parse_tool_args，修复失败才回退空参数
                args = parse_tool_args(
                    raw_args if isinstance(raw_args, str) else None
                ) or {}
            td = registry.get(tc.name)
            if td is None:
                output = f"错误：未知工具 {tc.name}"
            elif td.require_approval:
                hook = _permission_hook_var.get()
                approved = await hook(tc.name, args) if hook is not None else True
                if not approved:
                    output = f"已拒绝执行工具 {tc.name}（用户未授权）。"
                else:
                    with trace_span("tool.call", tool=tc.name):
                        output = await _dispatch(td, args)
            else:
                with trace_span("tool.call", tool=tc.name):
                    output = await _dispatch(td, args)
            tool_trace.append({"name": tc.name, "args": args, "output": output})
            convo.append(
                {"role": "tool", "tool_call_id": tc.id, "content": output}
            )

    # 工具轮数耗尽：做一次「纯文本收尾」调用（不带工具），避免把占位文案当最终答案
    _prior_nudges = {_FINAL_ROUND_NUDGE, _NUDGE_NOT_A_TOOL_CALL}
    if not (convo and convo[-1].get("role") == "user" and convo[-1].get("content") in _prior_nudges):
        convo.append({"role": "user", "content": _FINAL_ROUND_NUDGE})
    try:
        with trace_span("llm.call", rounds=rounds, final=True):
            final = await model.ainvoke(convo, tools=None)
        rounds += 1
        label = model.model_name() if hasattr(model, "model_name") else model.tier
        usage_total = merge_usage(usage_total, usage_from_result(final, label))
        record_llm_call(
            final.model or model.tier,
            final.usage.get("input", 0),
            final.usage.get("output", 0),
        )
        final_text = final.content or ""
        if _looks_like_tool_call_json(final_text):
            final_text = ""
        else:
            envelope = parse_answer_envelope(final_text)
            final_text = envelope or final_text
        if final_text:
            return {
                "text": final_text,
                "rounds": rounds,
                "tool_trace": tool_trace,
                "usage": usage_total,
            }
    except Exception:  # noqa: BLE001 - 收尾失败仍走占位文案兜底
        logger.warning("工具轮数耗尽后的纯文本收尾调用失败", exc_info=True)

    return {
        "text": "(达到最大工具轮数，未产出最终答案)",
        "rounds": rounds,
        "tool_trace": tool_trace,
        "usage": usage_total,
    }
