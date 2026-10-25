"""文件工具集（对齐 agent.md §9 execution/tools/file_tools.py）。

把 `VirtualFileSystem` 的能力包装为可注册的工具函数，并补充 §7.6 常用操作
（glob / append / delete）。所有路径都经 VFS 沙箱（防路径穿越），输出有界。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, Callable

from ..filesystem import VirtualFileSystem, bounded

MAX_MATCHES = 500


def _rel(vfs: VirtualFileSystem, path: Path) -> str:
    try:
        return path.relative_to(vfs.active_root).as_posix()
    except ValueError:
        return path.as_posix()


def glob_files(vfs: VirtualFileSystem, pattern: str, path: str = ".") -> str:
    """按 glob 模式递归匹配文件名。

    Args:
        pattern: glob 模式，如 `**/*.py`、`src/*.ts`。
        path: 搜索起始目录（相对工作目录）。
    """
    base = vfs._resolve(path)
    if not base.is_dir():
        return f"错误：{path} 不是目录"
    matches: list[str] = []
    for f in sorted(base.rglob("*")):
        if not f.is_file():
            continue
        rel_to_base = f.relative_to(base).as_posix()
        name = f.name
        if fnmatch.fnmatch(rel_to_base, pattern) or fnmatch.fnmatch(name, pattern):
            matches.append(_rel(vfs, f))
            if len(matches) >= MAX_MATCHES:
                return "\n".join(matches) + "\n... (已截断)"
    return "\n".join(matches) or "未找到匹配文件"


def append_file(vfs: VirtualFileSystem, path: str, content: str) -> str:
    """向文件末尾追加内容（不存在则新建）。"""
    fp = vfs._resolve(path)
    old = fp.read_text(encoding="utf-8") if fp.exists() else ""
    # 通过公开 write_file 写入合并内容，保证变更历史可回滚
    vfs.write_file(path, old + content)
    return f"已追加 {len(content)} 字符到 {path}"


def delete_file(vfs: VirtualFileSystem, path: str) -> str:
    """删除文件（记入变更历史，可回滚）。"""
    fp = vfs._resolve(path)
    if not fp.exists():
        return f"错误：文件不存在 {path}"
    if fp.is_dir():
        return f"错误：{path} 是目录，不能删除"
    old = fp.read_text(encoding="utf-8", errors="replace")
    _record(vfs, path, old)
    fp.unlink()
    return f"已删除 {path}"


class _FallbackEntry:
    def __init__(self, path: str, old: str | None) -> None:
        self.path = path
        self.old = old


def _record(vfs: VirtualFileSystem, path: str, old: str | None) -> None:
    """写入 VFS 变更历史（用于回滚）。"""
    try:
        from ..filesystem import _HistoryEntry  # 内部结构，同包使用

        vfs._history.append(_HistoryEntry(path, old))
    except Exception:  # noqa: BLE001
        vfs._history.append(_FallbackEntry(path, old))


def make_file_tools(vfs: VirtualFileSystem) -> dict[str, Callable[..., Any]]:
    """返回工具名 → 绑定函数 的字典（不含 scope）。"""

    def read_file(path: str) -> str:
        """读取工作区内文本文件的内容（输出有界）。"""
        return vfs.read_file(path)

    def write_file(path: str, content: str) -> str:
        """写入文件（覆盖或新建），内容以 UTF-8 保存。"""
        return vfs.write_file(path, content)

    def edit_file(path: str, old_text: str, new_text: str) -> str:
        """在文件中精确替换首次出现的文本（需完全匹配，含空白）。"""
        return vfs.edit_file(path, old_text, new_text)

    def ls(path: str = ".") -> str:
        """列出目录内容（子目录以 `/` 结尾）。"""
        return vfs.ls(path)

    def grep(pattern: str, path: str = ".") -> str:
        """用正则递归搜索文件内容，返回 `文件:行号: 内容`。"""
        return vfs.grep(pattern, path)

    def glob(pattern: str, path: str = ".") -> str:
        """按 glob 模式递归匹配文件名（如 `**/*.py`）。"""
        return glob_files(vfs, pattern, path)

    def append(path: str, content: str) -> str:
        """向文件末尾追加内容（不存在则新建）。"""
        return append_file(vfs, path, content)

    def delete(path: str) -> str:
        """删除工作区内文件（可回滚）。"""
        return delete_file(vfs, path)

    return {
        "read_file": read_file,
        "write_file": write_file,
        "edit_file": edit_file,
        "ls": ls,
        "grep": grep,
        "glob": glob,
        "append_file": append,
        "delete_file": delete,
    }


WRITE_TOOLS = frozenset({"write_file", "edit_file", "append_file", "delete_file"})
# 读工具同样过审批门控：沙箱/允许根内由 `ctx.request_permission` 自动放行（零弹窗），
# 越界读取才询问用户。此前读工具 `require_approval=False`，越界直接抛 "路径越界"，
# 与 PermissionManager `external_default=ask` 的语义不一致（读取无法申请授权）。
READ_TOOLS = frozenset({"read_file", "ls", "grep", "glob"})


def register_file_tools(
    registry: Any,
    vfs: VirtualFileSystem,
    *,
    scope: str = "location",
    names: "set[str] | None" = None,
) -> list[str]:
    """把文件工具注册进 `ToolRegistry`，返回注册的工具名列表。

    Args:
        registry: `execution.tool_registry.ToolRegistry` 实例。
        vfs: 绑定的虚拟文件系统。
        scope: 工具作用域（默认 location）。
        names: 仅注册这些工具名（默认全部）。
    """
    tools = make_file_tools(vfs)
    registered: list[str] = []
    for name, fn in tools.items():
        if names is not None and name not in names:
            continue
        registry.register_function(
            fn,
            name=name,
            description=(fn.__doc__ or "").strip(),
            scope=scope,
            require_approval=name in WRITE_TOOLS or name in READ_TOOLS,
        )
        registered.append(name)
    return registered


__all__ = [
    "MAX_MATCHES",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "append_file",
    "delete_file",
    "glob_files",
    "make_file_tools",
    "register_file_tools",
    "bounded",
]
