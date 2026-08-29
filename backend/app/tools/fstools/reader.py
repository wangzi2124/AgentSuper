"""拆分模块 `reader`（含 _file_not_found_envelope、_file_not_found_suggestion、_is_binary、_list_directory、tool_ls、tool_read_file）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──

import base64

import json

import os

import re

import shlex

import shutil

import signal

import stat

import subprocess

import time

from datetime import datetime

from pathlib import Path

from typing import Optional

from app.filesystem import GitignoreMatcher, ScanCache, get_project, glob_to_regex

from app.permission import get_manager as get_perm_mgr, NeedsPermission, current_session_workspace

# ── 跨子模块依赖（自动生成）──

from .common import DEFAULT_READ_LIMIT
from .common import MAX_BYTES
from .common import MAX_BYTES_LABEL
from .common import MAX_LINE_LENGTH
from .common import MAX_LINE_SUFFIX
from .common import SAMPLE_BYTES
from .common import _BINARY_EXTS
from .common import _MIME_MAP
from .common import _MULTIMODAL_EXTS
from .common import _TEXT_EXTS
from .common import _coerce_int
from .common import _env
from .workspace import _ensure_safe
from .workspace import _resolve
from .workspace import _scan_cache

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

def tool_ls(path: str = ".") -> dict:
    """列出指定目录下的文件和子目录，显示类型、大小和修改时间。

    与 opencode list 语义对齐：被 .gitignore 忽略的项（如 node_modules/.venv）不列出；
    worktree 之外的路径（自定义 root）不做忽略过滤。
    """
    target = _resolve(path)
    _ensure_safe(target, "read")
    if not target.is_dir():
        return _env("ls", f"Error: '{path}' is not a directory", error=True)
    rows = []
    for node in _scan_cache.list_dir(target):
        if node.ignored:
            continue
        entry = Path(node.path)
        try:
            st = entry.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            kind = "d" if node.type == "dir" else "f"
            rows.append(f"{kind} {size:>10}  {mtime}  {node.name}")
        except OSError:
            rows.append(f"? {'':>10}  {'':19}  {node.name}")
    return _env("ls", "\n".join(rows) if rows else "(empty)", entries=len(rows), path=str(target))

def _is_binary(path: Path) -> bool:
    """检测文件是否为二进制（opencode read.ts 同款逻辑：扩展名黑名单 + NUL 字节 + 非打印字符占比）。

    只读前 SAMPLE_BYTES(4KB) 样本判定，避免大文件整读。
    """
    ext = path.suffix.lower()
    if ext in _BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            sample = f.read(SAMPLE_BYTES)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    non_printable = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return non_printable / len(sample) > 0.3

def _file_not_found_suggestion(path_str: str, target: Path) -> str:
    """文件不存在时返回相似文件名建议（opencode read.ts 同款）。"""
    parent = target.parent
    base = target.name.lower()
    try:
        entries = [e.name for e in parent.iterdir()]
    except OSError:
        entries = []
    suggestions = [
        str(parent / entry) for entry in entries
        if entry.lower().find(base) != -1 or base.find(entry.lower()) != -1
    ][:3]
    if suggestions:
        return f"File not found: {path_str}\n\nDid you mean one of these?\n" + "\n".join(suggestions)
    return f"File not found: {path_str}"

def tool_read_file(path: str, offset: int = 1, limit: int = 0) -> dict:
    """读取文件内容，文本文件按行号格式返回并支持行偏移/限制，图片/PDF/音视频返回base64。

    与 opencode read.ts 对齐：limit 未传/<=0 时默认 DEFAULT_READ_LIMIT(2000) 行，
    输出受 MAX_BYTES(50KB) 字节硬上限约束（超出即截断并提示续读），
    offset 越界时报错。行级流式读取，不整文件载入内存。
    目录也支持读取：列出条目（子目录带尾部 '/'）。
    """
    target = _resolve(path)
    _ensure_safe(target, "read")
    offset = _coerce_int(offset, 1)
    limit = _coerce_int(limit, 0)
    if limit <= 0:
        limit = DEFAULT_READ_LIMIT
    if target.is_dir():
        return _list_directory(target, path, offset, limit)
    if not target.is_file():
        return _file_not_found_envelope(path, target)
    ext = target.suffix.lower()
    if ext in _MULTIMODAL_EXTS:
        try:
            raw = target.read_bytes()
            b64 = base64.b64encode(raw).decode("utf-8")
            mime = _MIME_MAP.get(ext, "application/octet-stream")
            return _env("read", f"data:{mime};base64,{b64}", mime=mime, truncated=False)
        except Exception as e:
            return _env("read", f"Error reading file: {e}", error=True)
    if ext not in _TEXT_EXTS and _is_binary(target):
        return _env("read", f"Cannot read binary file: {path}", error=True)
    start = offset - 1
    selected: list[str] = []
    count = 0
    bytes_used = 0
    cut = False
    more = False
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            for text in f:
                text = text.rstrip("\r\n")
                count += 1
                if count <= start:
                    continue
                if len(selected) >= limit:
                    more = True
                    continue
                line = text if len(text) <= MAX_LINE_LENGTH else text[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
                size = len(line.encode("utf-8")) + (1 if selected else 0)
                if bytes_used + size > MAX_BYTES:
                    cut = True
                    more = True
                    break
                selected.append(line)
                bytes_used += size
    except Exception as e:
        return _env("read", f"Error reading text file: {e}", error=True)
    if count < offset and not (count == 0 and offset == 1):
        return _env("read", f"Offset {offset} is out of range for this file ({count} lines)", error=True)
    content_lines = []
    for i, line in enumerate(selected):
        line_no = offset + i
        content_lines.append(f"{line_no}: {line}")
    content = "\n".join(content_lines)
    last_read = offset + len(selected) - 1
    next_offset = last_read + 1
    if cut:
        footer = f"\n\n(Output capped at {MAX_BYTES_LABEL}. Showing lines {offset}-{last_read}. Use offset={next_offset} to continue.)"
    elif more:
        footer = f"\n\n(Showing lines {offset}-{last_read} of {count}. Use offset={next_offset} to continue.)"
    else:
        footer = f"\n\n(End of file - total {count} lines)"
    truncated = bool(cut or more)
    return _env(
        "read",
        f"<file>\n{content}{footer}\n</file>",
        truncated=truncated,
        line_start=offset,
        line_end=last_read,
        total_lines=count,
        path=str(target),
    )

def _list_directory(target: Path, path_str: str, offset: int, limit: int) -> dict:
    """读取目录：列出条目（opencode read 目录语义：子目录带尾部 '/'，排序 dir 在前）。"""
    entries = _scan_cache.list_dir(target)
    rows: list[dict] = []
    for node in entries:
        if node.ignored:
            continue
        display = node.name + ("/" if node.type == "dir" else "")
        rows.append({"name": node.name, "type": node.type, "display": display})
    rows.sort(key=lambda r: (0 if r["type"] == "dir" else 1, r["name"].lower()))
    start = offset - 1
    sliced = rows[start:start + limit]
    truncated = start + len(sliced) < len(rows)
    lines = [r["display"] for r in sliced]
    note = (
        f"\n(Showing {len(sliced)} of {len(rows)} entries. Use offset={offset + len(sliced)} to continue.)"
        if truncated
        else f"\n({len(rows)} entries)"
    )
    output = [
        f"<path>{target}</path>",
        "<type>directory</type>",
        "<entries>",
        "\n".join(lines),
        note,
        "</entries>",
    ]
    return _env(
        "read",
        "\n".join(output),
        truncated=truncated,
        entries=len(rows),
        shown=len(sliced),
        path=str(target),
    )

def _file_not_found_envelope(path_str: str, target: Path) -> dict:
    return _env("read", _file_not_found_suggestion(path_str, target), error=True)



__all__ = ["_file_not_found_envelope", "_file_not_found_suggestion", "_is_binary", "_list_directory", "tool_ls", "tool_read_file"]
