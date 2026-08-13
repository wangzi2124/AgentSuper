"""
opencode 风格文件系统工具层。

对应 opencode packages/opencode/src/util/filesystem.ts:
  - normalize_path  -> normalizePath  (Windows 大小写规范化 / 展开 ~)
  - overlaps        -> overlaps       (两路径是否互为父子)
  - contains        -> contains       (child 是否在 parent 之下)
  - up              -> up             (向上取 n 级父目录)
  - find_up         -> findUp         (向上查找 .git / package.json 等)
  - glob_up         -> globUp         (向上逐级 glob)

以及对应 packages/opencode/src/file/index.ts 的目录扫描缓存
(Instance.state 缓存 + 基于 mtime 的增量刷新)。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .models import FileNode

logger = logging.getLogger(__name__)


def normalize_path(path: str | os.PathLike[str]) -> str:
    """规范化路径:展开 ~ 与相对路径,解析符号链接,返回绝对路径字符串。

    Windows 下 Path.resolve() 会自动做大小写归一(realpath 语义),
    对应 opencode 的 normalizePath(realpathSync.native)。
    """
    p = Path(os.path.expanduser(str(path)))
    try:
        resolved = p.resolve(strict=False)
    except OSError:
        resolved = p.absolute()
    return str(resolved)


def overlaps(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """两条路径是否存在包含关系(任一方是另一方的父路径,含相等)。"""
    pa = Path(normalize_path(a))
    pb = Path(normalize_path(b))
    return pa == pb or pa.is_relative_to(pb) or pb.is_relative_to(pa)


def contains(parent: str | os.PathLike[str], child: str | os.PathLike[str]) -> bool:
    """child 是否位于 parent 之下(含相等)。opencode contains() 语义。"""
    pp = Path(normalize_path(parent))
    cp = Path(normalize_path(child))
    return cp == pp or cp.is_relative_to(pp)


def up(path: str | os.PathLike[str], levels: int = 1) -> str:
    """向上取 levels 级父目录。"""
    p = Path(normalize_path(path))
    for _ in range(max(0, levels)):
        p = p.parent
    return str(p)


def find_up(
    names: str | Iterable[str],
    start: str | os.PathLike[str] | None = None,
) -> Optional[str]:
    """从 start(默认 cwd)向上逐级查找名为 names 中任意一项的目录/文件。

    用于发现 .git、.gitignore、package.json、pyproject.toml 等。
    返回找到项的绝对路径;未找到返回 None。
    """
    if isinstance(names, str):
        names = [names]
    wanted = set(names)
    current = Path(normalize_path(start) if start else Path.cwd())
    while True:
        try:
            for child in current.iterdir():
                if child.name in wanted:
                    return str(child)
        except OSError:
            pass
        if current.parent == current:
            return None
        current = current.parent


def glob_up(
    pattern: str,
    start: str | os.PathLike[str] | None = None,
    max_levels: int = 16,
) -> Iterator[str]:
    """从 start 向上逐级 glob 匹配,返回命中的文件绝对路径(升序)。"""
    current = Path(normalize_path(start) if start else Path.cwd())
    for _ in range(max_levels):
        try:
            for hit in sorted(current.glob(pattern)):
                if hit.is_file():
                    yield str(hit)
        except OSError:
            pass
        if current.parent == current:
            return
        current = current.parent


class ScanCache:
    """目录/文件列表缓存。

    对应 opencode file/index.ts 中基于 Instance.state 的扫描缓存:
    以 (目录, mtime_ns) 为键,目录内容变化(mtime 变化)时自动失效刷新。
    注意:目录 mtime 在子文件内容变更时不变化,因此编辑场景请调用 invalidate()。
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, list[FileNode]]] = {}

    def _scan(self, path: Path) -> tuple[float, list[FileNode]]:
        try:
            stat = path.stat()
        except OSError:
            return 0.0, []
        entries: list[FileNode] = []
        try:
            children = sorted(path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            children = []
        for child in children:
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            entries.append(
                FileNode(
                    name=child.name,
                    path=str(child),
                    absolute=str(child),
                    type="dir" if is_dir else "file",
                    ignored=False,
                )
            )
        return stat.st_mtime_ns, entries

    def list_dir(self, path: str | os.PathLike[str], force_refresh: bool = False) -> list[FileNode]:
        """返回目录内容列表;目录 mtime 未变且未强制刷新时命中缓存。"""
        key = normalize_path(path)
        mtime_ns, entries = self._scan(Path(key))
        cached = self._entries.get(key)
        if force_refresh or cached is None or cached[0] != mtime_ns:
            self._entries[key] = (mtime_ns, entries)
            return entries
        return cached[1]

    def invalidate(self, path: str | os.PathLike[str]) -> None:
        """使指定目录(及其最近祖先)的缓存失效,编辑文件后调用。"""
        key = normalize_path(path)
        self._entries.pop(key, None)
        # 使上级目录缓存一并失效(文件列表可能变化)
        parent = Path(key).parent
        self._entries.pop(str(parent), None)

    def clear(self) -> None:
        self._entries.clear()
