"""可排序 ID 生成器（对齐 opencode identifier.ts）。

ID 形如 `<prefix><time_encoded><random>`：
- 时间编码 = 毫秒时间戳 * 0x1000 + 同毫秒内计数器（base36），
  使 ID 按字典序递增即按创建时间排序，可直接用于排序/分页游标；
- 随机后缀（6 hex）保证多进程/重启后的唯一性。

前缀：ses_ / msg_ / prt_ / in_（对齐 opencode 的 ses_/msg_/prt_）。
"""

import os
import re
import threading
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# 会话 ID 会进入文件系统路径（模型切换备份等），只允许路径安全的字符。
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

_counter = 0
_counter_lock = threading.Lock()


def _to_base36(n: int) -> str:
    if n <= 0:
        return "0"
    chars: list[str] = []
    while n:
        n, rem = divmod(n, 36)
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def _time_encoded() -> str:
    global _counter
    with _counter_lock:
        _counter += 1
        seq = _counter & 0xFFF
    # 同毫秒内 seq < 0x1000，不会进位覆盖时间位，保证字典序即时间序
    n = int(time.time() * 1000) * 0x1000 + seq
    return _to_base36(n)


def new_id(prefix: str) -> str:
    """生成带前缀的可排序 ID。"""
    return f"{prefix}{_time_encoded()}{os.urandom(3).hex()}"


def validate_session_id(session_id: str) -> str:
    """校验调用方提供的会话 ID；非法值抛 ValueError（不做静默修正）。"""
    value = (session_id or "").strip()
    if not _SESSION_ID_RE.fullmatch(value):
        raise ValueError(
            "会话 ID 非法：只允许字母、数字、下划线和连字符（1-128 个字符）"
        )
    return value
