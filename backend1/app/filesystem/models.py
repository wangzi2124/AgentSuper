"""
opencode 风格文件系统数据模型。

对应 opencode packages/opencode/src/file/index.ts 中的:
  - File.Info     文件变更信息 (git diff --numstat)
  - File.Node     文件树节点 (name/path/absolute/type/ignored)
  - File.Content  文件内容模型 (mimeType/encoding/diff/patch/writeable)
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class FileStatus(str, Enum):
    """文件相对 git HEAD 的状态,对应 opencode File.Status。"""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    UNTRACKED = "untracked"
    UNCHANGED = "unchanged"


@dataclass
class FileInfo:
    """文件变更信息,对应 opencode File.Info (git diff --numstat 结果)。"""

    path: str                      # 相对仓库根的路径 (POSIX 风格)
    added: int = 0                 # 新增行数
    removed: int = 0               # 删除行数
    status: FileStatus = FileStatus.UNCHANGED


@dataclass
class FileNode:
    """文件树节点,对应 opencode File.Node。"""

    name: str                      # 仅名称
    path: str                      # 相对路径
    absolute: str                  # 绝对路径
    type: str                      # "file" | "dir"
    ignored: bool = False          # 是否被 .gitignore 忽略

    @property
    def is_dir(self) -> bool:
        return self.type == "dir"

    @property
    def is_file(self) -> bool:
        return self.type == "file"


@dataclass
class FileContent:
    """文件内容模型,对应 opencode File.Content。

    - mime_type: 通过 mimetypes 猜测的 MIME 类型
    - encoding:  "utf-8" / "binary" / 其它编码
    - writeable: 当前是否可写
    - diff:      相对 git HEAD 的差异 (可选)
    - patch:     待应用的补丁 (可选)
    """

    path: str
    content: str = ""
    mime_type: Optional[str] = None
    encoding: str = "utf-8"
    writeable: bool = True
    diff: Optional[str] = None
    patch: Optional[str] = None

    @classmethod
    def from_path(cls, path: str | Path, base: str | Path | None = None) -> "FileContent":
        """从磁盘读取文件并构造内容模型。

        base 提供时,path 字段保存为相对 base 的路径(与 opencode 相对 worktree 一致)。
        二进制文件编码记为 "binary",content 为空字符串。
        """
        p = Path(path)
        mime, _ = mimetypes.guess_type(str(p))
        raw: bytes = b""
        encoding = "utf-8"
        try:
            raw = p.read_bytes()
        except OSError:
            raw = b""
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw.decode("utf-16")
                encoding = "utf-16"
            except UnicodeDecodeError:
                content = ""
                encoding = "binary"
        rel = str(p) if base is None else p.relative_to(base).as_posix()
        return cls(
            path=rel,
            content=content,
            mime_type=mime,
            encoding=encoding,
            writeable=_is_writeable(p),
        )


def _is_writeable(path: Path) -> bool:
    """判断文件是否可写:不存在(可新建)或为普通文件且非符号链接。"""
    try:
        if path.exists():
            return path.is_file() and not path.is_symlink()
        return True
    except OSError:
        return False
