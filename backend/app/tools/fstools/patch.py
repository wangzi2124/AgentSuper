"""拆分模块 `patch`（含 _patch_add_body、_patch_apply_hunks、_patch_split_sections、_patch_update_hunks、tool_apply_patch）。

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

from .common import _env
from .workspace import _ensure_safe
from .workspace import _resolve
from .workspace import _scan_cache
from .writer import _convert_line_ending
from .writer import _detect_line_ending
from .writer import _normalize_line_endings
from .writer import _read_text_raw
from .writer import _write_text_raw

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

def _patch_split_sections(text: str) -> list[tuple[str, str, str]]:
    """解析 apply_patch 补丁文本，返回 [(action, path, body)]。

    action ∈ add/update/delete；body 为文件操作体的原始行（不含头部）。
    对齐 apply_patch.txt 的 Begin/End Patch + Add/Delete/Update File 语义。
    """
    lines = text.splitlines()
    sections: list[tuple[str, str, str]] = []
    current: str | None = None
    current_path = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            sections.append((current, current_path, "\n".join(body).rstrip("\n")))
            body = []

    for line in lines:
        stripped = line.strip()
        if stripped == "*** Begin Patch":
            continue
        if stripped == "*** End Patch":
            break
        if stripped.startswith("*** Add File:"):
            flush()
            current = "add"
            current_path = stripped[len("*** Add File:"):].strip()
            continue
        if stripped.startswith("*** Delete File:"):
            flush()
            current = "delete"
            current_path = stripped[len("*** Delete File:"):].strip()
            continue
        if stripped.startswith("*** Update File:"):
            flush()
            current = "update"
            current_path = stripped[len("*** Update File:"):].strip()
            continue
        if stripped.startswith("*** Move to:"):
            # 对齐 opencode：move 暂不支持 → 作为更新操作的一部分忽略
            continue
        if current is not None:
            body.append(line)
    flush()
    return sections

def _patch_add_body(body: str) -> str:
    """add 文件体：每行必须 + 前缀，去除前缀并补结尾换行。"""
    out_lines: list[str] = []
    for raw in body.splitlines():
        if raw.startswith("+"):
            out_lines.append(raw[1:])
        else:
            out_lines.append(raw)
    text = "\n".join(out_lines)
    return text if text.endswith("\n") else text + "\n"

def _patch_update_hunks(body: str) -> list[tuple[str, str]]:
    """把 update 操作体解析为 (old_block, new_block) hunk 列表。

    hunk 内：` ` 为上下文行（保留在两侧），`-` 为删除行（仅 old），
    `+` 为新增行（仅 new）。@@ 头部行忽略。
    """
    hunks: list[tuple[str, str]] = []
    old_lines: list[str] = []
    new_lines: list[str] = []
    for raw in body.splitlines():
        if raw.startswith("@@"):
            hunks.append(("\n".join(old_lines), "\n".join(new_lines)))
            old_lines, new_lines = [], []
            continue
        if not raw:
            continue
        prefix, rest = raw[0], raw[1:]
        if prefix in (" ",):
            old_lines.append(rest)
            new_lines.append(rest)
        elif prefix == "-":
            old_lines.append(rest)
        elif prefix == "+":
            new_lines.append(rest)
    hunks.append(("\n".join(old_lines), "\n".join(new_lines)))
    return hunks

def _patch_apply_hunks(content: str, hunks: list[tuple[str, str]]) -> str:
    """按 hunk 顺序把 old_block 替换为 new_block；找不到则模糊匹配（行内 trim 比较）。"""
    lines = content.splitlines()
    for old_block, new_block in hunks:
        if not old_block.strip():
            # 纯新增：定位到内容末尾（近似语义：追加）
            lines = lines + new_block.splitlines()
            continue
        target = old_block.splitlines()
        idx = -1
        for i in range(len(lines) - len(target) + 1):
            if lines[i:i + len(target)] == target:
                idx = i
                break
        if idx == -1:
            trimmed_target = [t.strip() for t in target]
            for i in range(len(lines) - len(target) + 1):
                if [l.strip() for l in lines[i:i + len(target)]] == trimmed_target:
                    idx = i
                    break
        if idx == -1:
            raise ValueError(f"Patch hunk not found in file:\n{old_block[:200]}")
        lines = lines[:idx] + new_block.splitlines() + lines[idx + len(target):]
    return "\n".join(lines)

def tool_apply_patch(patch_text: str) -> dict:
    """应用 apply_patch 格式的补丁：支持 Add File / Update File / Delete File 操作。

    对齐 apply_patch.txt 语义：
      *** Begin Patch
      *** Add File: <path>
      +<content line>
      *** Update File: <path>
      @@ <anchor>
      -<old line>
      +<new line>
      *** Delete File: <path>
      *** End Patch
    补丁按顺序逐条应用；某个操作失败时已应用的部分保留并明确报告。
    """
    if not patch_text or not patch_text.strip():
        return _env("apply_patch", "Error: patchText is required", error=True)
    sections = _patch_split_sections(patch_text)
    if not sections:
        return _env("apply_patch", "Error: patch rejected: empty patch", error=True)
    applied: list[str] = []
    try:
        for action, rel_path, body in sections:
            target = _resolve(rel_path)
            _ensure_safe(target, "write")
            if action == "add":
                if target.exists():
                    raise ValueError(f"File already exists: {rel_path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_text_raw(target, _patch_add_body(body))
                applied.append(f"A {rel_path}")
            elif action == "delete":
                if not target.exists():
                    raise ValueError(f"File not found: {rel_path}")
                target.unlink()
                applied.append(f"D {rel_path}")
            else:  # update
                if not target.is_file():
                    raise ValueError(f"File not found: {rel_path}")
                text, has_bom = _read_text_raw(target)
                ending = _detect_line_ending(text)
                hunks = _patch_update_hunks(body)
                if not hunks:
                    raise ValueError(f"Update hunk is empty: {rel_path}")
                # 归一化换行后应用，再转换回原文件行尾（对齐 edit.ts 行尾保留）
                normalized = _normalize_line_endings(text)
                normalized = _patch_apply_hunks(normalized, hunks)
                content_new = _convert_line_ending(normalized, ending)
                _write_text_raw(target, content_new, has_bom)
                applied.append(f"M {rel_path}")
            _scan_cache.invalidate(target.parent)
    except NeedsPermission:
        raise
    except Exception as e:
        prefix = (
            f"Error: Unable to apply patch at {rel_path}"
            if not applied
            else f"Error: Patch partially applied before failing at {rel_path}. Applied: {', '.join(applied)}"
        )
        return _env("apply_patch", f"{prefix}\n{e}", error=True, applied=applied)
    return _env("apply_patch", "Applied patch sequentially:\n" + "\n".join(applied), applied=applied)



__all__ = ["_patch_add_body", "_patch_apply_hunks", "_patch_split_sections", "_patch_update_hunks", "tool_apply_patch"]
