"""
FileSystem — opencode @opencode-ai/core/filesystem 移植（FS 服务抽象层）。

对齐 packages/core/src/filesystem.ts：
  - read(path)              读取文件原始字节
  - list(path)              列出目录条目(Entry)
  - find(input)             按文件名模式搜索
  - glob(input)             按 glob 模式搜索
  - grep(input)             按正则搜索内容

与 AgentSuper 既有文件工具的关系：本服务层负责"定位 + 列表 + 搜索"的
无副作用操作,写/编辑/执行等副作用操作仍由 file_tools.py(权限层之上)实现。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import search as _search
from .fsutil import DirEntry, is_dir, read_directory_entries


@dataclass
class Entry:
    path: str
    type: str  # "file" | "dir"


@dataclass
class Match:
    entry: Entry
    line: int
    column: int
    text: str


class FileSystem:
    """对齐 opencode FileSystem service 的只读文件系统服务。"""

    def __init__(
        self,
        root: str | None = None,
        resolve: Optional[Callable[[str], str]] = None,
    ) -> None:
        """root 为定位基准目录(绝对路径);resolve 可注入自定义路径解析(默认根目录拼接)。"""
        self.root = Path(root).resolve() if root else None
        self._resolve = resolve

    def _locate(self, path: str) -> Path:
        """将(可能相对的)输入路径解析为绝对路径。"""
        if self._resolve is not None:
            return Path(self._resolve(path))
        if Path(path).is_absolute():
            return Path(path)
        if self.root is None:
            return Path(path).absolute()
        return (self.root / path).resolve()

    def read(self, path: str) -> bytes:
        """读取文件原始字节。"""
        return self._locate(path).read_bytes()

    def list(self, path: str) -> list[DirEntry]:
        """列出目录条目。"""
        return read_directory_entries(self._locate(path))

    def glob(self, pattern: str, path: str, limit: int = 100) -> list[Entry]:
        """按 glob 模式列出路径(绝对路径)。"""
        target = self._locate(path)
        results = _search.glob(_search.GlobInput(pattern=pattern, path=str(target), limit=limit))
        return [Entry(path=e.path, type=e.type) for e in results]

    def grep(self, pattern: str, path: str, include: str = "", limit: int = 100) -> list[Match]:
        """按正则搜索文件内容。"""
        target = self._locate(path)
        results = _search.grep(_search.GrepInput(pattern=pattern, path=str(target), include=include, limit=limit))
        return [Match(entry=Entry(path=m.path, type="file"), line=m.line, column=m.column, text=m.text) for m in results]

    def find(self, pattern: str, path: str, limit: int = 100) -> list[Entry]:
        """按文件名模式搜索。"""
        target = self._locate(path)
        results = _search.find_files(pattern, str(target), limit=limit)
        return [Entry(path=e.path, type=e.type) for e in results]


class LocationResolvingError(ValueError):
    """路径无法解析到允许的位置。"""


def assert_location(location: str, inside: str, message: str = "") -> None:
    """断言 location 位于 inside 之下(用于外部目录越界检查,对齐 external-directory.ts)。"""
    from .fsutil import contains
    if not contains(inside, location):
        raise LocationResolvingError(message or f"Path '{location}' is outside '{inside}'")
