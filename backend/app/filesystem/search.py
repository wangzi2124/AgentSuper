"""
FileSystemSearch — opencode @opencode-ai/core/filesystem/search 移植。

对齐 packages/core/src/filesystem/search.ts：
  - glob(input)  按 glob 模式列出文件(绝对路径,limit 截断)
  - grep(input)  按正则/glob 搜索文件内容(分组为文件 + 行级命中)
  - find(input)  按文件名模式搜索

实现依赖 ripgrep(优先)或纯 Python 回退,均尊重 .gitignore 语义。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .gitignore import GitignoreMatcher, glob_to_regex
from .ripgrep import MAX_RECORD_BYTES, find as rg_find, ripgrep_binary


@dataclass
class GlobInput:
    pattern: str
    path: str
    limit: int = 100


@dataclass
class GrepInput:
    pattern: str
    path: str
    include: str = ""
    limit: int = 100


@dataclass
class Entry:
    path: str
    type: str = "file"  # "file" | "dir"


@dataclass
class Match:
    path: str
    line: int
    column: int
    text: str


def _sort_by_mtime(entries: list[Entry]) -> list[Entry]:
    def key(e: Entry) -> float:
        try:
            return Path(e.path).stat().st_mtime
        except OSError:
            return 0.0
    return sorted(entries, key=key, reverse=True)


def _python_glob(input: GlobInput) -> list[Entry]:
    """纯 Python glob:GitignoreMatcher 过滤 + glob_to_regex 匹配。"""
    rx = glob_to_regex(input.pattern)
    root = Path(input.path)
    matcher = GitignoreMatcher(root)
    results: list[Entry] = []
    for dirpath, dirs, files in matcher.walk(root):
        for d in dirs:
            try:
                rel = d.relative_to(root).as_posix()
            except ValueError:
                rel = d.name
            if rx.match(rel):
                results.append(Entry(path=str(d.resolve()), type="dir"))
        for f in files:
            try:
                rel = f.relative_to(root).as_posix()
            except ValueError:
                rel = f.name
            if rx.match(rel):
                results.append(Entry(path=str(f.resolve()), type="file"))
            if len(results) >= input.limit:
                return results[:input.limit]
    return results[:input.limit]


def glob(input: GlobInput) -> list[Entry]:
    """按 glob 模式列出路径(绝对路径)。优先 rg --files,否则纯 Python。"""
    binary = ripgrep_binary()
    if binary:
        try:
            return _rg_glob(binary, input)
        except Exception:
            pass
    return _python_glob(input)


def _rg_glob(binary: str, input: GlobInput) -> list[Entry]:
    import subprocess
    results: list[Entry] = []
    try:
        proc = subprocess.run(
            [binary, "--files", "--glob", input.pattern, "."],
            cwd=input.path, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return _python_glob(input)
    if proc.returncode not in (0, 1):
        return _python_glob(input)
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        p = Path(input.path) / line.strip()
        results.append(Entry(path=str(p.resolve()), type="dir" if p.is_dir() else "file"))
        if len(results) >= input.limit:
            break
    return results


def _python_grep(input: GrepInput) -> list[Match]:
    """纯 Python grep:GitignoreMatcher 裁剪 + 正则逐行匹配。"""
    try:
        rx = re.compile(input.pattern)
    except re.error:
        rx = None
    file_rx = glob_to_regex(input.include) if input.include else None
    root = Path(input.path)
    matcher = GitignoreMatcher(root)
    results: list[Match] = []
    for dirpath, dirs, files in matcher.walk(root):
        for f in files:
            if len(results) >= input.limit:
                return results[:input.limit]
            if file_rx is not None:
                try:
                    rel_f = f.relative_to(root).as_posix()
                except ValueError:
                    rel_f = f.name
                if not file_rx.match(rel_f):
                    continue
            try:
                if f.stat().st_size > MAX_RECORD_BYTES:
                    continue
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for lineno, raw_line in enumerate(fh, start=1):
                        if len(results) >= input.limit:
                            return results[:input.limit]
                        line = raw_line.rstrip("\r\n")
                        if rx is None:
                            if input.pattern in line:
                                col = line.find(input.pattern) + 1
                                results.append(Match(path=str(f.resolve()), line=lineno, column=col, text=line))
                            continue
                        for m in rx.finditer(line):
                            results.append(Match(path=str(f.resolve()), line=lineno, column=m.start() + 1, text=line))
                            if len(results) >= input.limit:
                                return results[:input.limit]
            except OSError:
                continue
    return results[:input.limit]


def grep(input: GrepInput) -> list[Match]:
    """按正则搜索文件内容(忽略二进制/被忽略路径)。优先 rg,否则纯 Python。"""
    binary = ripgrep_binary()
    if binary:
        try:
            return _rg_grep(binary, input)
        except Exception:
            pass
    return _python_grep(input)


def _rg_grep(binary: str, input: GrepInput) -> list[Match]:
    import subprocess
    results: list[Match] = []
    cmd = [binary, "--no-heading", "--line-number", "--column", input.pattern]
    if input.include:
        cmd += ["--glob", input.include]
    cmd.append(".")
    try:
        proc = subprocess.run(cmd, cwd=input.path, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return _python_grep(input)
    if proc.returncode == 2:
        return []  # invalid pattern
    if proc.returncode not in (0, 1):
        return _python_grep(input)
    for raw in proc.stdout.splitlines():
        if len(results) >= input.limit:
            break
        m = re.match(r"^([^:]+):(\d+):(\d+):(.*)$", raw)
        if not m:
            continue
        rel, ln, col, text = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        results.append(Match(path=str((Path(input.path) / rel).resolve()), line=ln, column=col, text=text))
    return results[:input.limit]


def find_files(pattern: str, path: str, limit: int = 100) -> list[Entry]:
    """按文件名模式搜索(递归,gitignore 过滤)。"""
    rx = glob_to_regex(pattern)
    root = Path(path)
    matcher = GitignoreMatcher(root)
    results: list[Entry] = []
    for dirpath, dirs, files in matcher.walk(root):
        for f in files:
            try:
                rel = f.relative_to(root).as_posix()
            except ValueError:
                rel = f.name
            if rx.match(rel) or rx.match(f.name):
                results.append(Entry(path=str(f.resolve()), type="file"))
                if len(results) >= limit:
                    return results
    return results
