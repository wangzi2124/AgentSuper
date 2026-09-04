"""拆分模块 `workspace`（含 _WORKSPACE_FALLBACK、_ensure_safe、_gitignore_checker、_gitignore_matcher、_is_read_allowed、_matcher_cache、_resolve、_scan_cache、_workspace）。

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

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

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

# 回退基准：项目上下文未初始化时使用 backend/（历史行为）

_WORKSPACE_FALLBACK = Path(__file__).resolve().parents[2]

_matcher_cache: dict[str, GitignoreMatcher] = {}

def _gitignore_matcher() -> GitignoreMatcher | None:
    """按 worktree 惰性构建 .gitignore 匹配器(含 .gitignore 文件 mtime 缓存)。"""
    ws = _workspace()
    key = str(ws)
    matcher = _matcher_cache.get(key)
    if matcher is None:
        try:
            matcher = GitignoreMatcher(ws)
        except Exception:
            matcher = None
        _matcher_cache[key] = matcher
    return matcher

def _gitignore_checker(path: Path, is_dir: bool) -> bool:
    matcher = _gitignore_matcher()
    if matcher is None:
        return False
    try:
        return matcher.is_ignored(path, is_dir)
    except OSError:
        return False

_scan_cache = ScanCache(ignored_checker=_gitignore_checker)

def _workspace() -> Path:
    """统一路径基准：git worktree（仓库根）。未初始化项目上下文时回退 backend/。

    对应 opencode instance-context.ts 的 worktree（git root）：相对路径、glob/grep
    输出、命令校验均以 worktree 为基准，而不是硬编码 backend/。
    """
    try:
        return Path(get_project().worktree)
    except Exception:
        return _WORKSPACE_FALLBACK

def _resolve(path_str: str) -> Path:
    """将路径字符串解析为绝对路径，相对路径基于当前会话工作目录解析。

    [会话目录] 本会话绑定了工作目录（opencode ctx.directory）时，相对路径
    以该目录为基准；否则回退到项目 worktree（git 仓库根）。

    [Windows] 末尾反斜杠会被去掉——".git\\" 在 PowerShell 模式中非法
    （\\ 是转义字符，尾部无后续字符则报错）。
    """
    p = Path(path_str)
    if not p.is_absolute():
        base = current_session_workspace() or str(_workspace())
        p = Path(base) / p
    resolved = p.resolve()
    # Windows 下 Path.resolve() 可能保留尾部反斜杠（如 "E:\\x\\.git\\"）
    # → PowerShell -Filter/-Like 模式报 "\\ 在模式末尾非法"。
    # 统一去掉尾部分隔符，保证路径规范化。
    s = str(resolved)
    if os.name == "nt" and (s.endswith("\\") or s.endswith("/")):
        s = s.rstrip("\\/")
        resolved = Path(s)
    return resolved

def _ensure_safe(path: Path, operation: str = "write") -> None:
    """检查路径访问权限，不允许时抛出PermissionError或NeedsPermission异常。"""
    resolved = path.resolve()
    mgr = get_perm_mgr()
    decision = mgr.check(str(resolved), operation)
    if decision == "allow":
        return
    if decision == "deny":
        raise PermissionError(f"Access denied: '{path}' is outside workspace or protected")
    raise NeedsPermission(str(resolved), operation)

def _is_read_allowed(path: Path) -> bool:
    """判断路径是否允许读取（工作区内敏感文件/系统路径/未授权外部路径返回 False）。"""
    mgr = get_perm_mgr()
    return mgr.check(str(path), "read") == "allow"



__all__ = ["_WORKSPACE_FALLBACK", "_ensure_safe", "_gitignore_checker", "_gitignore_matcher", "_is_read_allowed", "_matcher_cache", "_resolve", "_scan_cache", "_workspace"]
