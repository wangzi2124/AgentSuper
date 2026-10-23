"""历史消息归一化（agent-4 §2.1 / §5）。

- 原生路径：统一 ``tool_calls`` 形状、参数转 JSON 字符串；孤儿 ``tool`` 消息
  （没有对应调用 id，常见于跨模型切换）转成文本，避免 400 / role 冲突。
- 回退路径：``assistant.tool_calls`` 与 ``role=tool`` 全部文本化，用定界符包裹，
  新模型看不到它读不懂的 ``tool_call_id``。
"""

from __future__ import annotations

import json
from typing import Any

from .delimiters import render_tool_call, render_tool_result


def _arg_text(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return "{}"
    return str(value)


def _call_parts(call: Any) -> tuple[str, str, str]:
    """兼容 OpenAI dict / 简化 dict / 对象三种形状 → (id, name, arguments)。"""
    if isinstance(call, dict):
        ident = str(call.get("id") or "")
        fn = call.get("function")
        if isinstance(fn, dict):
            return ident, str(fn.get("name") or ""), _arg_text(fn.get("arguments"))
        return ident, str(call.get("name") or ""), _arg_text(call.get("arguments"))
    ident = str(getattr(call, "id", "") or "")
    fn = getattr(call, "function", None)
    if fn is not None:
        return (
            ident,
            str(getattr(fn, "name", "") or ""),
            _arg_text(getattr(fn, "arguments", None)),
        )
    return ident, str(getattr(call, "name", "") or ""), _arg_text(getattr(call, "arguments", None))


def _native_calls(calls: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for call in calls:
        ident, name, args = _call_parts(call)
        if not name:
            continue
        out.append(
            {
                "id": ident or f"call_{len(out)}",
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
        )
    return out


def _textify_tool_calls(calls: list[Any], content: str) -> str:
    blocks = [
        render_tool_call(name, args)
        for _, name, args in (_call_parts(c) for c in calls)
        if name
    ]
    if not blocks:
        return content or ""
    head = (content or "").strip()
    body = "\n".join(blocks)
    return f"{head}\n{body}".strip() if head else body


def _merge_same_role(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """连续相同 role（user/assistant）合并，保留 tool/system 边界。"""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        if (
            out
            and role in ("user", "assistant")
            and out[-1].get("role") == role
            and not out[-1].get("tool_calls")
            and not msg.get("tool_calls")
        ):
            prev = str(out[-1].get("content") or "")
            cur = str(msg.get("content") or "")
            out[-1]["content"] = f"{prev}\n\n{cur}".strip()
            continue
        out.append(msg)
    return out


def normalize_native(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    valid_ids: set[str] = set()
    id_to_name: dict[str, str] = {}
    for raw in messages:
        msg = dict(raw)
        role = str(msg.get("role") or "user")
        if role == "assistant":
            calls = msg.get("tool_calls") or []
            if calls:
                norm = _native_calls(calls)
                for c in norm:
                    valid_ids.add(c["id"])
                    id_to_name[c["id"]] = c["function"]["name"]
                msg["tool_calls"] = norm
            else:
                msg.pop("tool_calls", None)
        elif role == "tool":
            ident = str(msg.get("tool_call_id") or "")
            if ident and ident in valid_ids:
                msg.setdefault("name", id_to_name.get(ident, ""))
            else:
                name = id_to_name.get(ident) or msg.get("name") or "tool"
                msg = {
                    "role": "user",
                    "content": render_tool_result(str(name), str(msg.get("content") or "")),
                }
        out.append(msg)
    return _merge_same_role(out)


def normalize_fallback(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    id_to_name: dict[str, str] = {}
    for raw in messages:
        msg = dict(raw)
        role = str(msg.get("role") or "user")
        if role == "assistant":
            calls = msg.pop("tool_calls", None) or []
            for call in calls:
                ident, name, _ = _call_parts(call)
                if ident:
                    id_to_name[ident] = name
            msg["content"] = _textify_tool_calls(calls, str(msg.get("content") or ""))
        elif role == "tool":
            ident = str(msg.get("tool_call_id") or "")
            name = id_to_name.get(ident) or msg.get("name") or "tool"
            msg = {
                "role": "user",
                "content": render_tool_result(str(name), str(msg.get("content") or "")),
            }
        out.append(msg)
    return _merge_same_role(out)


def normalize_history(messages: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "fallback":
        return normalize_fallback(messages)
    return normalize_native(messages)
