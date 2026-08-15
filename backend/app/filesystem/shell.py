"""
Shell — opencode @opencode-ai/core/shell 移植（shell 识别与参数构造）。

对齐 packages/opencode/src/shell.ts：根据平台返回可用的 shell 名称、
powershell 标志与参数构造。
"""
from __future__ import annotations

import os

_POWERSHELL_EXES = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})
_BASH_EXES = frozenset({"bash", "bash.exe", "sh", "zsh"})
_CMD_EXES = frozenset({"cmd", "cmd.exe"})


def name(shell: str) -> str:
    """规范化 shell 名称(去扩展名/路径)。"""
    base = os.path.basename(shell).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def ps(shell: str) -> bool:
    """shell 是否为 PowerShell。"""
    return name(shell) in _POWERSHELL_EXES


def posix(shell: str) -> bool:
    """shell 是否为 POSIX 系(bash/sh/zsh)。"""
    return name(shell) in _BASH_EXES


def acceptable(shell: str) -> bool:
    """shell 是否被本实现接受(Windows: powershell/cmd/bash;POSIX: bash/sh/zsh)。"""
    if shell.lower() in _CMD_EXES:
        return True
    return ps(shell) or posix(shell)


def platform_shell() -> str:
    """返回当前平台推荐的 shell 名称。"""
    if os.name == "nt":
        return "powershell" if _powershell_available() else "cmd"
    return "bash"


def _powershell_available() -> bool:
    try:
        import shutil
        return shutil.which("powershell") is not None or shutil.which("pwsh") is not None
    except Exception:
        return False


def shell_args(shell: str, command: str) -> list[str]:
    """按 shell 类型构造执行参数列表(供子进程 exec 使用)。"""
    n = name(shell)
    if n in _POWERSHELL_EXES:
        return [shell, "-NoProfile", "-Command", command]
    if n in _BASH_EXES:
        return [shell, "-c", command]
    if n in _CMD_EXES:
        return [shell, "/c", command]
    raise ValueError(f"Unsupported shell: {shell}")
