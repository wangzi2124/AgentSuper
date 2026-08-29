"""拆分模块 `writer`（含 _EDIT_REPLACERS、_convert_line_ending、_detect_line_ending、_edit_block_anchor_replacer、_edit_context_aware_replacer、_edit_escape_normalized_replacer、_edit_indentation_flexible_replacer、_edit_levenshtein、_edit_line_positions、_edit_line_trimmed_replacer、_edit_multi_occurrence_replacer、_edit_replace、_edit_simple_replacer、_edit_trimmed_boundary_replacer、_edit_whitespace_normalized_replacer、_normalize_line_endings、_read_text_raw、_write_text_raw、tool_append_file、tool_delete_file、tool_edit_file、tool_rename_file、tool_write_file）。

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

from .common import _coerce_bool
from .common import _env
from .workspace import _ensure_safe
from .workspace import _resolve
from .workspace import _scan_cache

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

def _detect_line_ending(text: str) -> str:
    """检测文本行尾（opencode edit.ts detectLineEnding 同款）：\r\n 出现次数多于 \n 视为 CRLF。"""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"

def _normalize_line_endings(text: str) -> str:
    """将 CRLF 归一化为 LF（opencode edit.ts normalizeLineEndings 同款）。"""
    return text.replace("\r\n", "\n")

def _convert_line_ending(text: str, ending: str) -> str:
    """将 LF 行尾统一转换为目标行尾（opencode edit.ts convertToLineEnding 同款）。"""
    if ending == "\n":
        return text
    return text.replace("\n", ending)

def _read_text_raw(path: Path) -> tuple[str, bool]:
    """以 newline="" 原样读取文本（不做 universal newlines 转换），返回 (剥离BOM后的文本, 是否有BOM)。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    has_bom = text.startswith("\ufeff")
    return (text[1:] if has_bom else text), has_bom

def _write_text_raw(path: Path, text: str, has_bom: bool = False) -> None:
    """以 newline="" 原样写入文本（不做 os.linesep 转换），可选补回 UTF-8 BOM。"""
    if has_bom and not text.startswith("\ufeff"):
        text = "\ufeff" + text
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)

def tool_write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """创建新文件并写入内容。overwrite=True 时允许覆盖已存在的文件。

    行尾按内容原样写入（newline=""），不做系统换行符转换。
    """
    target = _resolve(path)
    _ensure_safe(target, "write")
    overwrite = _coerce_bool(overwrite)
    if target.exists() and not overwrite:
        return _env("write", f"Error: file already exists: {path} (use overwrite=True to overwrite, or edit_file to modify)", error=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        _write_text_raw(target, str(content))
        action = "Overwritten" if existed else "Created"
        _scan_cache.invalidate(target.parent)
        size = target.stat().st_size
        return _env("write", f"{action} {path} ({size} bytes)", path=str(target), size=size, action=action.lower())
    except Exception as e:
        return _env("write", f"Error writing file: {e}", error=True)

def tool_append_file(path: str, content: str) -> dict:
    """向文件追加内容（文件不存在则创建）。用于分段写入大文件：先 tool_write_file 写首段，再多次 append。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        with open(target, "a", encoding="utf-8", newline="") as f:
            f.write(str(content))
        total = target.stat().st_size
        action = "Appended to" if existed else "Created"
        _scan_cache.invalidate(target.parent)
        return _env("append", f"{action} {path} ({total} bytes total)", path=str(target), size=total)
    except Exception as e:
        return _env("append", f"Error appending to file: {e}", error=True)

def _edit_levenshtein(a: str, b: str) -> int:
    """Levenshtein 距离（opencode edit.ts levenshtein 移植）。"""
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(b)]

def _edit_line_positions(lines, start_line, end_line) -> tuple[int, int]:
    """计算 lines[start_line..end_line] 在整体字符串中的 [start, end) 位置。"""
    start = 0
    for k in range(start_line):
        start += len(lines[k]) + 1
    end = start
    for k in range(start_line, end_line + 1):
        end += len(lines[k])
        if k < end_line:
            end += 1
    return start, end

def _edit_simple_replacer(content: str, find: str):
    yield find

def _edit_line_trimmed_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    for i in range(0, len(original_lines) - len(search_lines) + 1):
        matches = all(
            original_lines[i + j].strip() == search_lines[j].strip()
            for j in range(len(search_lines))
        )
        if matches:
            start, end = _edit_line_positions(original_lines, i, i + len(search_lines) - 1)
            yield content[start:end]

def _edit_block_anchor_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if len(search_lines) < 3:
        return
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    first_line_search = search_lines[0].strip()
    last_line_search = search_lines[-1].strip()
    search_block_size = len(search_lines)

    candidates: list[tuple[int, int]] = []
    for i in range(len(original_lines)):
        if original_lines[i].strip() != first_line_search:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last_line_search:
                candidates.append((i, j))
                break

    if not candidates:
        return

    if len(candidates) == 1:
        start_line, end_line = candidates[0]
        actual_block_size = end_line - start_line + 1
        similarity = 0.0
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)
        if lines_to_check > 0:
            j = 1
            while j < search_block_size - 1 and j < actual_block_size - 1:
                original_line = original_lines[start_line + j].strip()
                search_line = search_lines[j].strip()
                max_len = max(len(original_line), len(search_line))
                if max_len > 0:
                    distance = _edit_levenshtein(original_line, search_line)
                    similarity += (1 - distance / max_len) / lines_to_check
                    if similarity >= 0.0:
                        break
                j += 1
        else:
            similarity = 1.0
        if similarity >= 0.0:
            start, end = _edit_line_positions(original_lines, start_line, end_line)
            yield content[start:end]
        return

    best: tuple[int, int] | None = None
    max_similarity = -1.0
    for start_line, end_line in candidates:
        actual_block_size = end_line - start_line + 1
        similarity = 0.0
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)
        if lines_to_check > 0:
            j = 1
            while j < search_block_size - 1 and j < actual_block_size - 1:
                original_line = original_lines[start_line + j].strip()
                search_line = search_lines[j].strip()
                max_len = max(len(original_line), len(search_line))
                if max_len > 0:
                    similarity += 1 - _edit_levenshtein(original_line, search_line) / max_len
                j += 1
            similarity /= lines_to_check
        else:
            similarity = 1.0
        if similarity > max_similarity:
            max_similarity = similarity
            best = (start_line, end_line)
    if max_similarity >= 0.3 and best:
        start, end = _edit_line_positions(original_lines, best[0], best[1])
        yield content[start:end]

def _edit_whitespace_normalized_replacer(content: str, find: str):
    import re
    normalize_ws = lambda text: re.sub(r"\s+", " ", text).strip()
    normalized_find = normalize_ws(find)
    lines = content.split("\n")
    for line in lines:
        if normalize_ws(line) == normalized_find:
            yield line
        else:
            normalized_line = normalize_ws(line)
            if normalized_find and normalized_line.find(normalized_find) != -1:
                words = find.strip().split()
                if words:
                    pattern = r"\s+".join(re.escape(w) for w in words)
                    try:
                        m = re.search(pattern, line)
                        if m:
                            yield m.group(0)
                    except re.error:
                        pass
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(0, len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if normalize_ws(block) == normalized_find:
                yield block

def _edit_indentation_flexible_replacer(content: str, find: str):
    import re
    def remove_indentation(text: str) -> str:
        lines = text.split("\n")
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return text
        min_indent = min(
            len(m.group(1)) if (m := re.match(r"^(\s*)", l)) else 0 for l in non_empty
        )
        return "\n".join(l if not l.strip() else l[min_indent:] for l in lines)

    normalized_find = remove_indentation(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(0, len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i:i + len(find_lines)])
        if remove_indentation(block) == normalized_find:
            yield block

def _edit_escape_normalized_replacer(content: str, find: str):
    import re
    _ESCAPE_MAP = {
        "n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"',
        "`": "`", "\\": "\\", "$": "$", "\n": "\n",
    }
    def unescape_string(s: str) -> str:
        return re.sub(r"\\([ntr'\"`\\\n$])", lambda m: _ESCAPE_MAP.get(m.group(1), m.group(0)), s)

    unescaped_find = unescape_string(find)
    if content.find(unescaped_find) != -1:
        yield unescaped_find
    lines = content.split("\n")
    find_lines = unescaped_find.split("\n")
    for i in range(0, len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if unescape_string(block) == unescaped_find:
            yield block

def _edit_trimmed_boundary_replacer(content: str, find: str):
    trimmed_find = find.strip()
    if trimmed_find == find:
        return
    if content.find(trimmed_find) != -1:
        yield trimmed_find
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(0, len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if block.strip() == trimmed_find:
            yield block

def _edit_context_aware_replacer(content: str, find: str):
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines and find_lines[-1] == "":
        find_lines.pop()
    content_lines = content.split("\n")
    first_line = find_lines[0].strip()
    last_line = find_lines[-1].strip()
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first_line:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() != last_line:
                continue
            block_lines = content_lines[i:j + 1]
            block = "\n".join(block_lines)
            if len(block_lines) == len(find_lines):
                matching = 0
                total_non_empty = 0
                for k in range(1, len(block_lines) - 1):
                    block_line = block_lines[k].strip()
                    find_line = find_lines[k].strip()
                    if block_line or find_line:
                        total_non_empty += 1
                        if block_line == find_line:
                            matching += 1
                if total_non_empty == 0 or matching / total_non_empty >= 0.5:
                    yield block
            break

def _edit_multi_occurrence_replacer(content: str, find: str):
    start_index = 0
    while True:
        index = content.find(find, start_index)
        if index == -1:
            break
        yield find
        start_index = index + len(find)

_EDIT_REPLACERS = [
    _edit_simple_replacer,
    _edit_line_trimmed_replacer,
    _edit_block_anchor_replacer,
    _edit_whitespace_normalized_replacer,
    _edit_indentation_flexible_replacer,
    _edit_escape_normalized_replacer,
    _edit_trimmed_boundary_replacer,
    _edit_context_aware_replacer,
    _edit_multi_occurrence_replacer,
]

def _edit_replace(content: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """opencode edit.ts replace() 移植：模糊匹配 + 多处匹配时报错（除非 replace_all）。"""
    if old_string == new_string:
        raise ValueError("oldString and newString must be different")

    not_found = True
    for replacer in _EDIT_REPLACERS:
        for search in replacer(content, old_string):
            index = content.find(search)
            if index == -1:
                continue
            not_found = False
            if replace_all:
                return content.replace(search, new_string)
            last_index = content.rfind(search)
            if index != last_index:
                continue
            return content[:index] + new_string + content[index + len(search):]
    if not_found:
        raise ValueError("oldString not found in content")
    raise ValueError(
        "Found multiple matches for oldString. Provide more surrounding lines in oldString to identify the correct match."
    )

def tool_edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict:
    """在文件中查找并替换指定字符串。支持模糊匹配；oldString 匹配到多处时报错（除非 replace_all=True）。

    与 opencode edit.ts 对齐：读取/写入均保留原文件行尾（LF 或 CRLF）与 UTF-8 BOM；
    old/new 字符串会归一化后转换到目标文件的行尾再做匹配，避免换行符静默损坏。
    """
    target = _resolve(path)
    _ensure_safe(target, "write")
    replace_all = _coerce_bool(replace_all)
    if not target.is_file():
        return _env("edit", f"File {path} not found", error=True)
    try:
        text, has_bom = _read_text_raw(target)
    except Exception as e:
        return _env("edit", f"Error reading file: {e}", error=True)
    ending = _detect_line_ending(text)
    try:
        if old_string == "":
            content_new = _convert_line_ending(_normalize_line_endings(new_string), ending)
        else:
            old = _convert_line_ending(_normalize_line_endings(old_string), ending)
            replacement = _convert_line_ending(_normalize_line_endings(new_string), ending)
            content_new = _edit_replace(text, old, replacement, replace_all)
    except ValueError as e:
        return _env("edit", f"Error: {e}", error=True)
    try:
        _write_text_raw(target, content_new, has_bom)
        _scan_cache.invalidate(target.parent)
        return _env("edit", f"Edited {path}", path=str(target), replace_all=replace_all)
    except Exception as e:
        return _env("edit", f"Error writing file: {e}", error=True)

def tool_delete_file(path: str) -> dict:
    """删除指定文件或空目录（仅限工作区内）。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    if not target.exists():
        return _env("delete", f"Error: not found: {path}", error=True)
    if target.is_dir():
        try:
            target.rmdir()
            _scan_cache.invalidate(target.parent)
            return _env("delete", f"Deleted directory {path}", path=str(target), kind="dir")
        except OSError as e:
            return _env("delete", f"Error deleting directory: {e}", error=True)
    try:
        target.unlink()
        _scan_cache.invalidate(target.parent)
        return _env("delete", f"Deleted {path}", path=str(target), kind="file")
    except Exception as e:
        return _env("delete", f"Error deleting file: {e}", error=True)

def tool_rename_file(path: str, new_path: str) -> dict:
    """重命名或移动文件/目录到新路径。"""
    src = _resolve(path)
    dst = _resolve(new_path)
    _ensure_safe(src, "write")
    _ensure_safe(dst, "write")
    if not src.exists():
        return _env("rename", f"Error: source not found: {path}", error=True)
    if dst.exists():
        return _env("rename", f"Error: destination already exists: {new_path}", error=True)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        _scan_cache.invalidate(src.parent)
        _scan_cache.invalidate(dst.parent)
        return _env("rename", f"Renamed {path} -> {new_path}", src=str(src), dst=str(dst))
    except Exception as e:
        return _env("rename", f"Error renaming: {e}", error=True)



__all__ = ["_EDIT_REPLACERS", "_convert_line_ending", "_detect_line_ending", "_edit_block_anchor_replacer", "_edit_context_aware_replacer", "_edit_escape_normalized_replacer", "_edit_indentation_flexible_replacer", "_edit_levenshtein", "_edit_line_positions", "_edit_line_trimmed_replacer", "_edit_multi_occurrence_replacer", "_edit_replace", "_edit_simple_replacer", "_edit_trimmed_boundary_replacer", "_edit_whitespace_normalized_replacer", "_normalize_line_endings", "_read_text_raw", "_write_text_raw", "tool_append_file", "tool_delete_file", "tool_edit_file", "tool_rename_file", "tool_write_file"]
