"""LLM tool-call 参数 JSON 修复模块。

LLM 返回的 tool_calls[].function.arguments 经常不是合法 JSON（未闭合字符串、
单引号、尾逗号、markdown 代码围栏、Python 风格字面量、未转义换行等）。
主链路（graph.py）、子 Agent（sub_tools.py）、supervisor 分解都直接/间接依赖，
这里提供一个自包含的修复函数：先尝试 json.loads，失败后逐步修复，
最后用括号配平 + 字符串扫描的容错解析兜底。
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def parse_json_value(raw: str | None) -> object:
    """解析任意 JSON 值（dict/list/...），带修复。彻底失败返回 None。

    供 supervisor 分解、其他非 dict 结构复用。
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    text = _strip_code_fence(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fixed = _light_fix(text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 结构重构：补闭合引号/括号、去尾逗号，再解析
    rebuilt = _rebuild_structure(fixed)
    try:
        return json.loads(rebuilt)
    except json.JSONDecodeError:
        pass

    logger.warning("Unable to repair json value (%.200s...)", text)
    return None


def parse_tool_args(raw: str | None) -> dict | None:
    """解析 LLM 工具参数，尽量修复常见坏 JSON。

    成功返回 dict（可能为空 dict），彻底失败返回 None。
    """
    data = parse_json_value(raw)
    return data if isinstance(data, dict) else None


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` / ``` ... ``` 围栏及其前后散文。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text


def _light_fix(text: str) -> str:
    """做不破坏字符串内容的轻量替换。"""
    # 去掉注释（// 与 /* */），只处理引号外的部分；
    # 同时把双引号字符串内的原始换行转义为 \\n（JSON 字符串不允许字面换行，
    # 这是 tool_edit_file 多行 old_string/new_string 触发 Unterminated string 的主因）。
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch in "\r\n":
                out.append("\\n")
            else:
                out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        # 尾逗号：`,` 后紧跟空白+`}`/`]` → 去掉逗号
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i = j
                continue
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            # 行注释 → 跳过到行尾
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch == "'":
            # 单引号字符串 → 转为双引号（内容里的双引号转义、单引号保留）
            out.append('"')
            i += 1
            buf: list[str] = []
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    buf.append(c)
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if c == "'":
                    i += 1
                    break
                if c == '"':
                    buf.append("\\\"")
                elif c == "\n":
                    buf.append("\\n")
                else:
                    buf.append(c)
                i += 1
            out.append("".join(buf))
            out.append('"')
            continue
        out.append(ch)
        i += 1
    text = "".join(out)
    # Python 字面量
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    return text


def _rebuild_structure(text: str) -> str:
    """扫描并重构：补全未闭合字符串的引号、未配平的右括号，剥离前后非 JSON 文本。

    只保证"结构上可被 json.loads 接受"；不做语义正确性保证。
    """
    text = text.strip()
    # 定位第一个结构化起点（{ 或 [），之前的散文丢弃
    start = len(text)
    for ch in "{[": 
        idx = text.find(ch)
        if 0 <= idx < start:
            start = idx
    if start == len(text):
        return text
    text = text[start:]

    out: list[str] = []
    stack: list[str] = []   # 未配平的开括号（'}' → ']'）
    in_str = False
    escape = False
    started = False        # 是否已进入结构化内容（用于剥离闭括号后的散文）
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            else:
                out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = True
            started = True
            out.append(ch)
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            started = True
            out.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            out.append(ch)
            # 顶层已闭合 → 丢弃之后的散文
            if not stack:
                break
        elif ch in ",:":
            if started:
                out.append(ch)
        elif started and ch not in " \t\r\n":
            # 结构内的字面量字符（数字/true/false/null）原样保留，其余忽略
            out.append(ch)
        i += 1

    # 字符串未闭合 → 补上引号
    if in_str:
        out.append('"')
    # 括号未配平 → 按栈顺序补右括号
    for close_ch in reversed(stack):
        out.append(close_ch)
    return "".join(out)