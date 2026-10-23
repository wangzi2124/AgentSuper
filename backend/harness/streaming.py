"""流式增量解析（agent-4 §2.2 / §6-P1）。

- ``StreamToolCallParser``：回退路径，定界符闭合即产出调用，不等待整段响应结束。
- ``PreludeSuppressor``：原生路径，模型先输出解释性文本再输出内嵌 JSON 调用时，
  检测到 ``{"name":`` / ``[{"name":`` 后抑制前置文本。
"""

from __future__ import annotations

from typing import Any

from .delimiters import (
    ARGS_CLOSE,
    ARGS_OPEN,
    NAME_CLOSE,
    NAME_OPEN,
    HarnessToolCall,
    parse_arguments,
)


def _partial_suffix_len(buf: str, token: str) -> int:
    """buf 末尾是 token 前缀的最长长度（不含完整 token）。"""
    for k in range(min(len(token) - 1, len(buf)), 0, -1):
        if buf.endswith(token[:k]):
            return k
    return 0


class StreamToolCallParser:
    """文本回退路径的流式解析器。"""

    def __init__(self) -> None:
        self._buf = ""
        self._state = "text"
        self._name = ""

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        if chunk:
            self._buf += chunk
        events: list[dict[str, Any]] = []
        while True:
            if self._state == "text":
                idx = self._buf.find(NAME_OPEN)
                if idx >= 0:
                    if idx:
                        events.append({"type": "text", "text": self._buf[:idx]})
                    self._buf = self._buf[idx + len(NAME_OPEN):]
                    self._state = "name"
                    continue
                keep = _partial_suffix_len(self._buf, NAME_OPEN)
                if len(self._buf) > keep:
                    cut = len(self._buf) - keep
                    events.append({"type": "text", "text": self._buf[:cut]})
                    self._buf = self._buf[cut:]
                break

            if self._state == "name":
                idx = self._buf.find(NAME_CLOSE)
                if idx < 0:
                    break
                self._name = self._buf[:idx].strip()
                self._buf = self._buf[idx + len(NAME_CLOSE):]
                self._state = "args_open"
                continue

            if self._state == "args_open":
                stripped = self._buf.lstrip()
                if not stripped:
                    self._buf = ""
                    break
                if stripped.startswith(ARGS_OPEN):
                    consumed = len(self._buf) - len(stripped) + len(ARGS_OPEN)
                    self._buf = self._buf[consumed:]
                    self._state = "args"
                    continue
                if stripped.startswith(NAME_OPEN):
                    events.append(
                        {
                            "type": "tool_call",
                            "call": HarnessToolCall(
                                name=self._name,
                                arguments="{}",
                                error="缺少参数定界符",
                            ),
                        }
                    )
                    self._state = "text"
                    continue
                if ARGS_OPEN.startswith(stripped):
                    self._buf = stripped
                    break
                # 无法识别的残片：丢弃标记，继续找下一个调用
                self._state = "text"
                continue

            if self._state == "args":
                idx = self._buf.find(ARGS_CLOSE)
                if idx < 0:
                    break
                raw = self._buf[:idx].strip() or "{}"
                parsed = parse_arguments(raw)
                events.append(
                    {
                        "type": "tool_call",
                        "call": HarnessToolCall(
                            name=self._name,
                            arguments=raw if parsed is None else _dump(parsed),
                            parsed=parsed,
                            error="" if parsed is not None else "参数不是合法 JSON",
                        ),
                    }
                )
                self._buf = self._buf[idx + len(ARGS_CLOSE):]
                self._state = "text"
                continue
        return events

    def finalize(self) -> list[dict[str, Any]]:
        """流结束时释放纯文本；未闭合的调用块按“未完成”丢弃。"""
        events: list[dict[str, Any]] = []
        if self._state == "text" and self._buf:
            events.append({"type": "text", "text": self._buf})
        self._buf = ""
        self._state = "text"
        self._name = ""
        return events


class PreludeSuppressor:
    """原生路径前置文本抑制器。

    在检测到内嵌 JSON 调用标记前先缓冲，避免把解释性文本推给用户；
    超过 ``max_buffer`` 字符仍无标记则整段放行（长回答不被无限延迟）。
    """

    _MARKERS = ('{"name":', '[{"name":')

    def __init__(self, max_buffer: int = 512) -> None:
        self._buf = ""
        self._max = max(1, int(max_buffer))
        self._decided = False

    def feed(self, chunk: str) -> str:
        if self._decided:
            return ""
        self._buf += chunk or ""
        if any(m in self._buf for m in self._MARKERS):
            self._decided = True
            self._buf = ""
            return ""
        if len(self._buf) < self._max:
            return ""
        self._decided = True
        out, self._buf = self._buf, ""
        return out

    def finalize(self) -> str:
        if self._decided:
            self._buf = ""
            return ""
        out = self._buf
        self._buf = ""
        return out


def _dump(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
