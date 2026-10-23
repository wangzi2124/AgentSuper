"""两条路径的统一适配器：输出 ``{name, arguments}``，Loop 不感知模型差异。"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from utils.json_repair import parse_json_value

from .capabilities import FALLBACK, NATIVE
from .delimiters import HarnessToolCall, parse_arguments, parse_tool_calls, render_tool_call

logger = logging.getLogger(__name__)

_FALLBACK_HEADER = "You have access to the following tools:"


def _tool_params(tool: dict[str, Any]) -> dict[str, Any]:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    params = fn.get("parameters") or {"type": "object", "properties": {}}
    return params if isinstance(params, dict) else {}


def build_fallback_prompt(tool_defs: list[dict[str, Any]]) -> str:
    """把 tools 定义渲染为系统提示词（agent-4 §2.2 的格式）。"""
    lines = [_FALLBACK_HEADER]
    for tool in tool_defs:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        desc = str(fn.get("description") or "").strip().replace("\n", " ")
        props = _tool_params(tool).get("properties") or {}
        params = {str(k): _param_type(v) for k, v in props.items()}
        lines.append(f"- {name}: {desc}")
        lines.append(f"  Parameters: {json.dumps(params, ensure_ascii=False)}")
    lines.extend(
        [
            "",
            "How to call tools:",
            "ꊰ▸function_name◂ꊰ",
            'ꊰ▹{"param": "value"}◃ꊰ',
            "",
            "一次只输出工具名行 + 参数 JSON 行；需要多个工具时可连续输出多组。",
            "工具执行结果会以「工具 <name> 的执行结果：」加参数定界符回灌给你。",
            "不要在普通回答中使用上述定界符。",
        ]
    )
    return "\n".join(lines)


def _param_type(schema: Any) -> str:
    if isinstance(schema, dict):
        return str(schema.get("type") or "string")
    return "string"


def inject_fallback_prompt(
    messages: list[dict[str, Any]], tool_defs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not tool_defs:
        return messages
    block = build_fallback_prompt(tool_defs)
    out = [dict(m) for m in messages]
    for msg in out:
        if msg.get("role") == "system":
            msg["content"] = f"{msg.get('content') or ''}\n\n{block}".strip()
            return out
    return [{"role": "system", "content": block}, *out]


def _brace_json(text: str, start: int) -> tuple[Any, int] | None:
    """从 start（指向 { 或 [）截取配平的 JSON 并解析。"""
    if start >= len(text) or text[start] not in "{[":
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                value = parse_json_value(text[start : i + 1])
                return (value, i + 1) if value is not None else None
    return None


def extract_embedded_native_calls(text: str) -> tuple[list[HarnessToolCall], str]:
    """原生路径：模型把调用写成正文（前置解释 + 内嵌 JSON）→ 提取并抑制前置文本。"""
    if not text or '"name"' not in text:
        return [], text
    markers = [text.find('{"name"'), text.find('[{"name"')]
    markers = [m for m in markers if m >= 0]
    if not markers:
        return [], text
    first = min(markers)
    prelude = text[:first].strip()
    calls: list[HarnessToolCall] = []
    pos = first
    while pos < len(text) and len(calls) < 16:
        idx = text.find("{", pos)
        if idx < 0:
            break
        got = _brace_json(text, idx)
        if got is None:
            break
        value, end = got
        items = value if isinstance(value, list) else [value]
        for item in items:
            call = _dict_to_call(item)
            if call is not None:
                calls.append(call)
        pos = end
        rest = text[pos:].lstrip()
        if not (rest.startswith("{") or rest.startswith("[{")):
            break
    return calls, prelude


def _dict_to_call(item: Any) -> HarnessToolCall | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("tool") or item.get("function")
    if isinstance(name, dict):
        args = name.get("arguments")
        name = name.get("name")
    else:
        args = item.get("arguments", item.get("parameters", item.get("args")))
    if not name:
        return None
    if isinstance(args, str):
        parsed = parse_arguments(args)
        return HarnessToolCall(
            name=str(name),
            arguments=args or "{}",
            parsed=parsed,
            error="" if parsed is not None else "参数不是合法 JSON",
        )
    args = args if isinstance(args, dict) else {}
    return HarnessToolCall(
        name=str(name),
        arguments=json.dumps(args, ensure_ascii=False),
        parsed=args,
    )


def _to_tool_call(call: HarnessToolCall) -> Any:
    from models.types import ToolCall

    return ToolCall(
        id=f"harness-{uuid.uuid4().hex[:12]}",
        name=call.name,
        arguments=call.arguments,
    )


def apply_result(result: Any, mode: str) -> Any:
    """归一化模型响应：统一成 ``tool_calls``，正文去掉调用块。"""
    from .delimiters import NAME_OPEN

    content = result.content or ""
    calls: list = list(result.tool_calls or [])

    if NAME_OPEN in content:
        parsed, cleaned = parse_tool_calls(content)
        if parsed:
            calls.extend(_to_tool_call(c) for c in parsed)
            result.content = cleaned

    if not calls and mode == NATIVE:
        embedded, prelude = extract_embedded_native_calls(result.content or "")
        if embedded:
            calls = [_to_tool_call(c) for c in embedded]
            result.content = prelude

    result.tool_calls = calls
    if calls and not result.finish_reason:
        result.finish_reason = "tool-calls"
    return result


def call_argument_error(call: Any) -> tuple[dict[str, Any] | None, str]:
    """解析单个工具调用参数；失败返回可直接回灌给模型的错误文本。"""
    raw = getattr(call, "arguments", None)
    if isinstance(raw, dict):
        return raw, ""
    text = "" if raw is None else str(raw)
    if not text.strip():
        return {}, ""
    parsed = parse_arguments(text)
    if parsed is None:
        snippet = text if len(text) <= 200 else text[:200] + "…"
        return None, (
            f"工具参数不是合法 JSON，请修正后重新调用（工具 {getattr(call, 'name', '?')}）。"
            f"原始参数：{snippet}"
        )
    return parsed, ""


__all__ = [
    "FALLBACK",
    "NATIVE",
    "apply_result",
    "build_fallback_prompt",
    "call_argument_error",
    "extract_embedded_native_calls",
    "inject_fallback_prompt",
    "render_tool_call",
]
