"""CodeAct 结构化结果、回灌格式与帧编解码（设计 §3.2 / §8）。

帧协议（单条 127.0.0.1 回环连接复用三通道，长度前缀 + JSON）：
  - 沙盒→宿主:  {"ch":"rpc",   "id":N, "fn":"write_file", "args":{...}}
  - 宿主→沙盒:  {"ch":"rpc_resp","id":N,"ok":true,"result":"..."}
                 {"ch":"rpc_resp","id":N,"ok":false,"error":{"code":"...","msg":"..."}}
  - 宿主→沙盒:  {"ch":"control","cmd":"cancel"}
  - 沙盒→宿主:  {"ch":"result","ok":true,"stdout":"...","stderr":"...","last_expr":"...","traceback":null,"error":null}
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any, Optional

# 帧头：4 字节大端长度 + JSON 负载
_HEADER = struct.Struct(">I")
_MAX_FRAME_BYTES = 16 * 1024 * 1024  # 单帧上限 16MB（防构造超大帧打爆宿主内存）


class FrameCodecError(Exception):
    pass


def encode_frame(payload: dict) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw) > _MAX_FRAME_BYTES:
        raise FrameCodecError(f"frame payload too large: {len(raw)} bytes")
    return _HEADER.pack(len(raw)) + raw


def decode_frame(data: bytes) -> dict:
    if len(data) < _HEADER.size:
        raise FrameCodecError("frame too short")
    (size,) = _HEADER.unpack_from(data)
    body = data[_HEADER.size : _HEADER.size + size]
    if size != len(body):
        raise FrameCodecError("incomplete frame body")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FrameCodecError(f"bad frame json: {e}")


def _head_tail(text: str, limit_chars: int) -> str:
    """head+tail 预截断（设计 §8 两级截断的第一级，沙盒侧防 IPC 爆）。

    limit <= 0 表示不截断。
    """
    if not text:
        return ""
    if limit_chars is None or limit_chars <= 0 or len(text) <= limit_chars:
        return text
    half = max(1, limit_chars // 2)
    omitted = len(text) - 2 * half
    head = text[:half]
    tail = text[-half:] if omitted > 0 else ""
    marker = f"\n…[omitted {omitted} chars]…\n" if omitted > 0 else "\n…\n"
    return head + marker + tail


@dataclass
class CodeActResult:
    """一次受控执行的结构化结果（设计 §8）。"""

    ok: bool
    stdout: str = ""
    stderr: str = ""
    last_expr: Optional[str] = None
    traceback: Optional[str] = None
    files: list[str] = field(default_factory=list)
    error: Optional[dict] = None
    duration_ms: float = 0.0

    def to_text(self, max_output_chars: int = 2000) -> str:
        """回灌给 LLM 的格式化文本（设计 §8）。"""
        status = "执行成功" if self.ok else "执行失败"
        parts = [f"[codeact] {status} ({self.duration_ms:.1f}s)"]
        if self.last_expr and str(self.last_expr).strip():
            parts.append("--- last expression ---")
            parts.append(_head_tail(str(self.last_expr), max_output_chars))
        if self.stdout and self.stdout.strip():
            parts.append("--- stdout ---")
            parts.append(_head_tail(self.stdout, max_output_chars))
        if self.stderr and self.stderr.strip():
            parts.append("--- stderr ---")
            parts.append(_head_tail(self.stderr, max_output_chars))
        if self.traceback and self.traceback.strip():
            parts.append("--- traceback ---")
            parts.append(self.traceback)
        if self.error:
            parts.append("--- error ---")
            parts.append(f"{self.error.get('code', '?')}: {self.error.get('msg', '')}")
        if self.files:
            parts.append("--- files ---")
            parts.extend(self.files)
        return "\n".join(parts)

    @classmethod
    def from_frame(cls, data: dict, duration_ms: float = 0.0) -> "CodeActResult":
        """沙盒 result 帧 → 结构化结果。"""
        return cls(
            ok=bool(data.get("ok", False)),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            last_expr=data.get("last_expr"),
            traceback=data.get("traceback"),
            error=data.get("error"),
            duration_ms=duration_ms,
        )

    def to_frame(self) -> dict:
        return {
            "ch": "result",
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "last_expr": self.last_expr,
            "traceback": self.traceback,
            "error": self.error,
        }