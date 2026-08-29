"""拆分模块 `search`（含 tool_glob、tool_grep）。

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

from .common import MAX_LINE_LENGTH
from .common import _MULTIMODAL_EXTS
from .common import _coerce_bool
from .common import _coerce_int
from .common import _env
from .workspace import _ensure_safe
from .workspace import _gitignore_matcher
from .workspace import _is_read_allowed
from .workspace import _resolve

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

def tool_glob(pattern: str, path: str = ".") -> dict:
    """在指定目录中按glob模式搜索文件，返回匹配路径列表（opencode glob 语义：绝对路径，No files found 表示无结果）。"""
    root_path = _resolve(path)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return _env("glob", f"Error: '{root_path}' is not a directory", error=True)
    matcher = _gitignore_matcher()
    if matcher is not None:
        matches = [m for m in matcher.glob(pattern, top=root_path) if _is_read_allowed(m)]
    else:
        matches = [m for m in root_path.glob(pattern) if _is_read_allowed(m)]
    if not matches:
        return _env("glob", "No files found", matches=0)
    matches.sort(key=lambda m: os.path.getmtime(m), reverse=True)
    # 对齐 opencode glob.ts：输出绝对路径
    lines = [str(m.resolve()) for m in matches[:100]]
    truncated = len(matches) > 100
    if truncated:
        lines.append("... and {} more".format(len(matches) - 100))
        lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    return _env(
        "glob",
        "\n".join(lines),
        matches=min(len(matches), 100),
        total_matches=len(matches),
        truncated=truncated,
    )

def tool_grep(pattern: str, include: str = "", context: int = 0, count_only: bool = False, files_only: bool = False, path: str = ".") -> dict:
    """在指定目录的文本文件中按正则表达式搜索内容，支持文件过滤、上下文显示和目录范围。

    与 opencode grep.ts 对齐：输出绝对路径；空结果 "No files found"；
    有结果时头部 "Found N matches"（超限时 "(more matches available)"），按文件分组。
    """
    import re
    context = _coerce_int(context, 0)
    count_only = _coerce_bool(count_only)
    files_only = _coerce_bool(files_only)
    root_path = _resolve(path)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return _env("grep", f"Error: '{path}' is not a directory", error=True)
    use_re = re.compile(pattern, re.MULTILINE | re.DOTALL)
    file_pattern = include if include else "**/*"
    file_matches: list[tuple[Path, list[int]]] = []
    matcher = _gitignore_matcher()
    if matcher is not None:
        glob_rx = glob_to_regex(file_pattern)
        candidates: Iterable[Path] = (
            f
            for dirpath, dirs, files in matcher.walk(root_path)
            for f in files
        )
    else:
        glob_rx = None
        candidates = (f for f in root_path.glob(file_pattern) if f.is_file())
    for f in candidates:
        if not _is_read_allowed(f):
            continue
        if glob_rx is not None:
            try:
                rel = f.relative_to(root_path)
            except ValueError:
                rel = Path(f.name)
            if not glob_rx.match(rel.as_posix()):
                continue
        ext = f.suffix.lower()
        if ext in _MULTIMODAL_EXTS:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found_positions: list[int] = []
        for m in use_re.finditer(text):
            line_start = text[:m.start()].count("\n")
            found_positions.append(line_start)
        if found_positions:
            file_matches.append((f, found_positions))
    if not file_matches:
        return _env("grep", "No files found", matches=0, total_matches=0)
    file_matches.sort(key=lambda t: os.path.getmtime(t[0]), reverse=True)
    output: list[str] = []
    total_matches = 0
    truncated = False
    for f, found_positions in file_matches:
        rel = str(f.resolve())
        if files_only:
            if total_matches >= 100:
                truncated = True
                break
            output.append(rel)
            total_matches += 1
            continue
        if count_only:
            if total_matches >= 100:
                truncated = True
                break
            output.append(f"{rel}: {len(found_positions)} match(es)")
            total_matches += 1
            continue
        seen_lines: set[int] = set()
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except Exception:
            lines = []
        emitted = 0
        file_output: list[str] = []
        for lineno in found_positions:
            if lineno in seen_lines:
                continue
            seen_lines.add(lineno)
            if total_matches + emitted >= 100:
                truncated = True
                break
            emitted += 1
            start = max(0, lineno - context)
            end = min(len(lines), lineno + context + 1)
            for i in range(start, end):
                marker = ">" if i == lineno else " "
                line_text = lines[i].rstrip()
                if len(line_text) > MAX_LINE_LENGTH:
                    line_text = line_text[:MAX_LINE_LENGTH] + "..."
                file_output.append(f"  {marker} Line {i+1}: {line_text}")
            file_output.append("")
        if emitted:
            output.append(f"{rel}:")
            output.extend(file_output)
            total_matches += emitted
        if truncated:
            break
    if truncated:
        output.append(f"... and more. Showing first 100 matches.")
        output.append("(Results are truncated. Consider using a more specific path or pattern.)")
    body = "\n".join(output).rstrip()
    header = f"Found {total_matches} matches (more matches available)" if truncated else f"Found {total_matches} matches"
    return _env(
        "grep",
        f"{header}\n{body}",
        matches=total_matches,
        truncated=truncated,
        pattern=pattern,
    )



__all__ = ["tool_glob", "tool_grep"]
