"""厂商方言归一化（Vendor dialect）。

对齐 agent.md §4：模型调用统一经 `models/` 层。不同厂商/运行时把「工具调用」和
「推理内容」放在不同字段，需要在此归一化，业务层只面对统一的 content/tool_calls。

当前主要处理 **Ollama 流式方言**：litellm 的 Ollama 流式处理器不把工具调用转成标准
`tool_calls` 增量，而是以 `{"name": "<tool>", "arguments": {...}}` 的 JSON 文本逐字流入
`delta.content`；弱模型（qwen2.5-coder 等）常记不精确键名，故放宽别名并支持
`<tool_call>` 显式标记。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 弱模型（qwen2.5-coder 等）常记不精确键名，放宽别名：name 键兼容 function/tool/
# action，arguments 键兼容 parameters/args/params；并支持 <tool_call> 显式标记。
TCC_NAME_KEYS = ("name", "function", "tool", "tool_name", "action")
TCC_ARGS_KEYS = ("arguments", "parameters", "args", "params", "input")
TCC_MARKER_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
TCC_PATTERN = re.compile(
    r'^\s*\{\s*"name"\s*:\s*"([^"]*)"\s*,\s*"arguments"\s*:\s*(\{.*\})\s*\}\s*$', re.S
)
# 未知工具名时优先抽取的内嵌回复字段（qwen 常把答话放到 arguments.message/content）
TCC_INNER_KEYS = ("message", "content", "text", "answer", "result", "response")


def extract_tool_call_obj(content: str):
    """从文本中抽取工具调用对象（支持 <tool_call> 标记与 OpenAI message 信封）。

    - 扁平行：{"name": ..., "arguments": {...}}（键名可放宽别名）
    - OpenAI 信封：{"function": {"name": ..., "arguments": {...}}}
    返回 dict（含 name/arguments 之一）或 None。
    """
    if not content:
        return None
    m = TCC_MARKER_RE.search(content)
    raw = (m.group(1) if m else content).strip()
    if len(raw) < 2 or not (raw.startswith("{") and raw.endswith("}")):
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    # B. OpenAI message envelope：function 是 {name, arguments} 对象而不是工具名
    fn = obj.get("function")
    if isinstance(fn, dict) and (fn.get("name") or fn.get("arguments")):
        return {
            "name": (fn.get("name") or "").strip(),
            "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), dict) else {},
        }
    return obj


def tcc_prefix_ok(text: str) -> bool:
    """宽松判断 text 是否仍是 Ollama 流式工具调用 JSON 的合法前缀。

    流式 token 切分无定式（`{"`、`"name"` 逐字节流出），只要首个键是某个「名称键」或
    键名尚在流中，就认为仍可能是工具调用 JSON → 暂缓推送，避免把工具 JSON 闪给前端。
    """
    t = text.strip()
    if not t:
        return True
    if t.startswith("<tool_call>"):
        return "</tool_call>" not in t
    if not t.startswith("{"):
        return False
    rest = t[1:].lstrip()
    for nk in TCC_NAME_KEYS:
        key = f'"{nk}"'
        if rest.startswith(key):
            return True
        if len(rest) < len(key) and key.startswith(rest):
            return True  # 键名还在流中
    return False


def parse_streamed_tool_call(content: str):
    """识别流式注入 content 的工具调用文本，返回 {"name", "arguments"} 或 None。"""
    obj = extract_tool_call_obj(content)
    if not obj:
        return None
    name = None
    for k in TCC_NAME_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    if not name:
        return None
    args = None
    for k in TCC_ARGS_KEYS:
        v = obj.get(k)
        if isinstance(v, dict):
            args = v
            break
    if args is None:
        return None
    return {"name": name, "arguments": args}


def sanitize_tool_call_content(content: str, mounted_names: set[str] | None = None):
    """把可能残留的工具调用 JSON 文本清洗为可展示内容。

    - 命中已挂载工具名的独立工具调用 JSON → 返回 None（应丢掉的工具声明）；
      未挂载工具名 → 抽取 arguments 内嵌回复字段。
    - 不匹配 → 原样返回。
    """
    if not content:
        return content
    call = parse_streamed_tool_call(content)
    if not call:
        return content
    if mounted_names and call["name"] in mounted_names:
        return None
    for k in TCC_INNER_KEYS:
        v = call["arguments"].get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None
