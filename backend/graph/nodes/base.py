"""节点公共设施：工具 schema 生成 + 工具调用循环。"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import typing
from typing import Any, Awaitable, Callable

from execution.tool_registry import ToolDefinition, ToolRegistry
from models.router import TierModel
from observability.metrics import record_llm_call
from observability.tracing import trace_span

from ..usage import empty_usage, merge_usage, usage_from_result

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

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
            return {
                "text": result.content,
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
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
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

    return {
        "text": "(达到最大工具轮数，未产出最终答案)",
        "rounds": rounds,
        "tool_trace": tool_trace,
        "usage": usage_total,
    }
