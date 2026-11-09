"""Unified-diff patch tool without shelling out (opencode gap G-15)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from execution.filesystem import VirtualFileSystem

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class _Hunk:
    old_start: int
    lines: list[tuple[str, str]]


def _parse(diff: str) -> tuple[str, list[_Hunk]]:
    path = ""
    hunks: list[_Hunk] = []
    current: _Hunk | None = None
    for raw in diff.splitlines():
        if raw.startswith("--- ") or raw.startswith("+++ "):
            if raw.startswith("+++ "):
                candidate = raw[4:].strip().split("\t", 1)[0]
                if candidate and candidate != "/dev/null":
                    path = candidate
            continue
        match = _HUNK.match(raw)
        if match:
            current = _Hunk(int(match.group(1)), [])
            hunks.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("\\"):
            continue
        tag = raw[:1]
        if tag in (" ", "-", "+"):
            current.lines.append((tag, raw[1:]))
    return path, hunks


def _apply_hunk(lines: list[str], hunk: _Hunk) -> list[str]:
    index = max(0, hunk.old_start - 1)
    out = list(lines)
    cursor = index
    for tag, text in hunk.lines:
        if tag == " ":
            if cursor >= len(out) or out[cursor] != text:
                raise ValueError(f"context mismatch at line {cursor + 1}")
            cursor += 1
        elif tag == "-":
            if cursor >= len(out) or out[cursor] != text:
                raise ValueError(f"removal mismatch at line {cursor + 1}")
            out.pop(cursor)
        elif tag == "+":
            out.insert(cursor, text)
            cursor += 1
    return out


def make_patch_tools(vfs: VirtualFileSystem) -> dict[str, Any]:
    def apply_patch(diff: str) -> str:
        """在工作区内应用 unified diff（不调用 shell，失败返回错误）。"""
        path, hunks = _parse(diff)
        if not path or not hunks:
            return "错误：diff 缺少文件路径或 hunk"
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            return "错误：diff 路径必须是工作区内相对路径"
        existing = vfs.read_file(path)
        if existing.startswith("错误："):
            return existing
        lines = existing.splitlines()
        try:
            for hunk in hunks:
                lines = _apply_hunk(lines, hunk)
        except ValueError as exc:
            return f"错误：补丁应用失败：{exc}"
        out = vfs.write_file(
            path, "\n".join(lines) + ("\n" if existing.endswith("\n") else "")
        )
        if not out.startswith("错误："):
            try:
                from .file_tools import _after_mutation

                _after_mutation(vfs, path)
            except Exception:  # noqa: BLE001 - formatting must never break a patch
                pass
        return out

    return {"apply_patch": apply_patch}


__all__ = ["make_patch_tools"]
