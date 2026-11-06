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
    _split_trailing_json,
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
        self._pending_text = ""

    def _looks_like_pending_json(self, text: str) -> bool:
        import json

        stripped = text.lstrip()
        if not stripped.startswith("{"):
            return False
        try:
            _, end = json.JSONDecoder().raw_decode(stripped)
        except ValueError:
            # Incomplete JSON may still be the arguments emitted before the
            # function marker; hold it instead of leaking a partial object.
            return True
        tail = stripped[end:].strip()
        return not tail or NAME_OPEN.startswith(tail)

    def _finish_call(
        self,
        raw: str,
        parsed: dict | None,
        error: str = "",
    ) -> list[dict[str, Any]]:
        if not raw or raw == "{}":
            prefix, value, _ = _split_trailing_json(self._pending_text)
            if value is not None:
                self._pending_text = prefix
                raw = _dump(value)
                parsed = value
                error = ""
        events: list[dict[str, Any]] = []
        if self._pending_text:
            events.append({"type": "text", "text": self._pending_text})
            self._pending_text = ""
        if parsed is not None:
            raw = _dump(parsed)
        events.append(
            {
                "type": "tool_call",
                "call": HarnessToolCall(
                    name=self._name,
                    arguments=raw,
                    parsed=parsed,
                    error=error,
                ),
            }
        )
        self._name = ""
        return events

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        if chunk:
            self._buf += chunk
        events: list[dict[str, Any]] = []
        while True:
            if self._state == "text":
                idx = self._buf.find(NAME_OPEN)
                if idx >= 0:
                    self._pending_text += self._buf[:idx]
                    self._buf = self._buf[idx + len(NAME_OPEN):]
                    self._state = "name"
                    continue
                keep = _partial_suffix_len(self._buf, NAME_OPEN)
                if self._looks_like_pending_json(self._buf):
                    break
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
                    events.extend(
                        self._finish_call("{}", {}, "缺少参数定界符")
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
                events.extend(
                    self._finish_call(
                        raw,
                        parsed,
                        "" if parsed is not None else "参数不是合法 JSON",
                    )
                )
                self._buf = self._buf[idx + len(ARGS_CLOSE):]
                self._state = "text"
                continue
        return events

    def finalize(self) -> list[dict[str, Any]]:
        """流结束时释放纯文本；未闭合的调用块按“未完成”丢弃。"""
        events: list[dict[str, Any]] = []
        if self._state == "text":
            if self._pending_text:
                events.append({"type": "text", "text": self._pending_text})
            if self._buf:
                events.append({"type": "text", "text": self._buf})
        self._buf = ""
        self._pending_text = ""
        self._state = "text"
        self._name = ""
        return events


class PreludeSuppressor:
    """原生路径前置文本抑制器。

    在检测到内嵌 JSON 调用标记前先缓冲，避免把解释性文本推给用户；
    遇到换行或超过 ``max_buffer`` 字符仍无标记则放行已确定的部分，
    但继续扫描后续 chunk（长回答不被无限延迟，标记仍会被拦截）。
    """

    _MARKERS = ('{"name":', '[{"name":')

    def __init__(self, max_buffer: int = 64) -> None:
        self._buf = ""
        self._max = max(1, int(max_buffer))
        self._suppressing = False

    def _partial_marker_suffix(self) -> int:
        keep = 0
        for marker in self._MARKERS:
            for k in range(min(len(marker) - 1, len(self._buf)), 0, -1):
                if self._buf.endswith(marker[:k]):
                    keep = max(keep, k)
                    break
        return keep

    def feed(self, chunk: str) -> str:
        if self._suppressing:
            return ""
        self._buf += chunk or ""
        if any(m in self._buf for m in self._MARKERS):
            self._suppressing = True
            self._buf = ""
            return ""
        flush = 0
        newline = self._buf.rfind("\n")
        if newline >= 0:
            flush = newline + 1
        if len(self._buf) >= self._max:
            flush = max(flush, len(self._buf) - self._partial_marker_suffix())
        if flush <= 0:
            return ""
        out, self._buf = self._buf[:flush], self._buf[flush:]
        return out

    def finalize(self) -> str:
        if self._suppressing:
            self._buf = ""
            return ""
        out = self._buf
        self._buf = ""
        return out


def _dump(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
