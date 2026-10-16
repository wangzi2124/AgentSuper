"""
Ripgrep — opencode @opencode-ai/core/ripgrep 移植。

对齐 packages/core/src/ripgrep.ts：
  - RawMatch {filename, line, column, submatches}
  - MAX_RECORD_BYTES / MAX_SUBMATCHES 上限
  - InvalidPatternError / InternalRipgrepError 错误类型
  - find() 逐文件流式回调(onEntry)

实现策略：优先使用系统 rg 二进制(性能好、gitignore 语义完整)；未安装时
回退到纯 Python 实现(GitignoreMatcher.walk + 正则逐行匹配)，保证 glob/grep
语义一致且无第三方依赖(rg 在 Windows 开发机上可能未安装)。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .gitignore import GitignoreMatcher

logger = logging.getLogger(__name__)

MAX_RECORD_BYTES = 64 * 1024
MAX_SUBMATCHES = 100


class InvalidPatternError(ValueError):
    """正则模式无效(rg 会以非零退出码表示)。"""


class InternalRipgrepError(RuntimeError):
    """rg 内部错误(超时/执行失败等)。"""


@dataclass
class RawSubmatch:
    """一次匹配的子串定位,对应 opencode RawSubmatch。"""

    content: str
    start: int
    end: int


@dataclass
class RawMatch:
    """单行匹配结果,对应 opencode RawMatch。"""

    filename: str
    line: int  # 1-indexed
    column: int  # 1-indexed
    submatch: RawSubmatch
    line_text: str = ""


@dataclass
class FindInput:
    """find() 输入,对应 opencode FindInput。"""

    cwd: str
    pattern: str
    limit: int = 1000
    on_entry: Optional[Callable[[RawMatch], None]] = None


_rg_path: Optional[str] = None
_rg_lock = threading.Lock()


# backend/app/filesystem/ripgrep.py → parents[2] = backend/ 目录
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# rg.exe 候选位置（按序探测）：仓库内预置 bundled > 环境 PATH。
# data/bin 为官方推荐落点（后端已把 rg.exe 复制至此，跨重启稳定可用，
# 不依赖 shell PATH——winget/scoop 安装后新 PATH 对已运行进程不生效）。
_RG_CANDIDATES = [
    _BACKEND_DIR / "data" / "bin" / ("rg.exe" if os.name == "nt" else "rg"),
    _BACKEND_DIR / "bin" / ("rg.exe" if os.name == "nt" else "rg"),
]


def _existing(bin_dir: Path) -> Optional[str]:
    """返回目录中存在的 rg 可执行文件；不存在返回 None。"""
    try:
        exe = bin_dir.resolve()
    except OSError:
        return None
    return str(exe) if exe.is_file() else None


def ripgrep_binary() -> Optional[str]:
    """定位 rg 二进制(惰性缓存);未安装返回 None。

    探测顺序：仓库内预置（data/bin、bin）→ 系统 PATH(shutil.which)。
    """
    global _rg_path
    if _rg_path is not None:
        return _rg_path or None
    with _rg_lock:
        if _rg_path is not None:
            return _rg_path or None
        found: Optional[str] = None
        for cand in _RG_CANDIDATES:
            found = _existing(cand)
            if found:
                break
        if not found:
            try:
                found = shutil.which("rg")
            except Exception:
                found = None
        _rg_path = found or ""
        if found:
            logger.info("ripgrep binary: %s", found)
        return found


def _compile_pattern(pattern: str) -> "re.Pattern[str]":
    try:
        return re.compile(pattern)
    except re.error as e:
        raise InvalidPatternError(str(e)) from e


def _python_find(input: FindInput) -> None:
    """纯 Python 回退实现:按 .gitignore 遍历 + 正则逐行匹配。"""
    rx = _compile_pattern(input.pattern)
    matcher = GitignoreMatcher(input.cwd)
    emitted = 0
    for dirpath, dirs, files in matcher.walk(input.cwd):
        for f in files:
            if emitted >= input.limit:
                return
            try:
                rel = f.relative_to(input.cwd).as_posix()
            except ValueError:
                rel = f.name
            if f.stat().st_size > MAX_RECORD_BYTES:
                continue
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for lineno, raw_line in enumerate(fh, start=1):
                        if emitted >= input.limit:
                            return
                        line = raw_line.rstrip("\r\n")
                        for m in rx.finditer(line):
                            col = m.start() + 1
                            submatch = RawSubmatch(content=m.group(0), start=m.start(), end=m.end())
                            if input.on_entry:
                                input.on_entry(RawMatch(
                                    filename=rel, line=lineno, column=col,
                                    submatch=submatch, line_text=line,
                                ))
                                emitted += 1
                                if emitted >= input.limit:
                                    return
            except OSError:
                continue


def find(input: FindInput) -> list[RawMatch]:
    """搜索正则模式,返回匹配结果(受 limit 限制)。

    优先 rg 二进制(子进程、JSON 流式解析),否则纯 Python 回退。
    """
    results: list[RawMatch] = []

    def on_match(m: RawMatch) -> None:
        results.append(m)
        if input.on_entry:
            input.on_entry(m)

    binary = ripgrep_binary()
    if binary:
        try:
            _rg_find(binary, input, on_match)
            return results
        except InternalRipgrepError:
            pass  # 降级到纯 Python
    _python_find(FindInput(
        cwd=input.cwd, pattern=input.pattern, limit=input.limit, on_entry=on_match,
    ))
    return results


def _rg_find(binary: str, input: FindInput, on_match: Callable[[RawMatch], None]) -> None:
    """用 rg 子进程执行 find(--json 输出),失败时抛 InternalRipgrepError。"""
    try:
        proc = subprocess.run(
            [binary, "--json", "--line-number", "--column", input.pattern, "."],
            cwd=input.cwd, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        raise InternalRipgrepError(str(e)) from e
    if proc.returncode == 2:
        raise InvalidPatternError(proc.stderr.strip() or "invalid pattern")
    if proc.returncode not in (0, 1):
        raise InternalRipgrepError(proc.stderr.strip() or f"rg exited with {proc.returncode}")
    import json
    emitted = 0
    for raw in proc.stdout.splitlines():
        if emitted >= input.limit:
            break
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("type") != "match":
            continue
        data = rec.get("data", {})
        filename = data.get("path", {}).get("text", "")
        line = int(data.get("line_number", 0))
        col = int(data.get("absolute_offset", 0))
        submatches = data.get("submatches", [])
        if not submatches:
            continue
        first = submatches[0]
        content = first.get("match", {}).get("text", "")
        start = int(first.get("start", 0))
        end = int(first.get("end", 0))
        line_text = content  # rg JSON 不含整行文本;回退用 submatch 文本
        on_match(RawMatch(filename=filename, line=line, column=col,
                          submatch=RawSubmatch(content=content, start=start, end=end),
                          line_text=line_text))
        emitted += 1
