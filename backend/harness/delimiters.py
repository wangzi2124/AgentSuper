"""文本回退工具调用的定界符协议（agent-4 §2.2）。

生僻字符组合，正常文本几乎不会出现，避免 ``<<<TOOL_CALL>>>`` 式冲突。

调用格式（两行，参数可为单行 JSON）::

    ꊰ▸tool_read_file◂ꊰ
    ꊰ▹{"path": "a.txt"}◃ꊰ
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.json_repair import parse_json_value, parse_tool_args

NAME_OPEN = "ꊰ▸"
NAME_CLOSE = "◂ꊰ"
ARGS_OPEN = "ꊰ▹"
ARGS_CLOSE = "◃ꊰ"

_NAME_RE = re.compile(
    re.escape(NAME_OPEN) + r"\s*([^\s" + re.escape(NAME_CLOSE) + r"]+)\s*" + re.escape(NAME_CLOSE)
)
_ARGS_RE = re.compile(re.escape(ARGS_OPEN) + r"(.*?)" + re.escape(ARGS_CLOSE), re.S)


@dataclass
class HarnessToolCall:
    """两条路径统一输出：Loop 只认 ``name`` + ``arguments``（JSON 字符串）。"""

    name: str
    arguments: str = "{}"
    parsed: dict | None = field(default=None)
    error: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "arguments": self.arguments}


def render_tool_call(name: str, arguments: str | dict | None = None) -> str:
    if isinstance(arguments, (dict, list)):
        import json

        args = json.dumps(arguments, ensure_ascii=False)
    else:
        args = str(arguments or "{}")
    return f"{NAME_OPEN}{name}{NAME_CLOSE}\n{ARGS_OPEN}{args}{ARGS_CLOSE}"


def parse_arguments(raw: str | None) -> dict | None:
    """解析工具参数；明显非 JSON 的垃圾不得被 repair 成空 dict 冒充成功。"""
    text = (raw or "").strip()
    if not text:
        return {}
    direct = parse_json_value(text)
    if isinstance(direct, dict) and (direct or re.fullmatch(r"\{\s*\}", text)):
        return direct
    repaired = parse_tool_args(text)
    if isinstance(repaired, dict):
        if repaired or re.fullmatch(r"\{\s*\}", text):
            return repaired
    return None


def render_tool_result(name: str, output: str) -> str:
    """工具结果回灌文本（不再使用 NAME 定界符，避免被当成新的调用）。"""
    return f"工具 {name} 的执行结果：\n{ARGS_OPEN}{output}{ARGS_CLOSE}"


def parse_tool_calls(text: str) -> tuple[list[HarnessToolCall], str]:
    """从模型文本中提取调用，并返回去掉调用块后的正文。"""
    if not text or NAME_OPEN not in text:
        return [], text or ""

    calls: list[HarnessToolCall] = []
    spans: list[tuple[int, int]] = []
    names = list(_NAME_RE.finditer(text))
    for i, m in enumerate(names):
        name = m.group(1).strip()
        if not name:
            continue
        block_end = names[i + 1].start() if i + 1 < len(names) else len(text)
        args_m = _ARGS_RE.search(text, m.end(), block_end)
        if not args_m:
            calls.append(
                HarnessToolCall(name=name, arguments="{}", error="缺少参数定界符")
            )
            spans.append((m.start(), m.end()))
            continue
        raw = args_m.group(1).strip() or "{}"
        parsed = parse_arguments(raw)
        error = "" if parsed is not None else "参数不是合法 JSON"
        calls.append(
            HarnessToolCall(
                name=name,
                arguments=raw if parsed is None else _dumps(parsed),
                parsed=parsed,
                error=error,
            )
        )
        spans.append((m.start(), args_m.end()))

    cleaned = text
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + cleaned[end:]
    return calls, cleaned.strip()


def _dumps(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
