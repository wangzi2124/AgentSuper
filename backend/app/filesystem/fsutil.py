"""
FSUtil — opencode @opencode-ai/core/fs-util 移植（纯路径助手 + 文件系统操作）。

对齐 packages/core/src/fs-util.ts：
  - normalizePath / windowsPath / contains / overlaps / resolve / mimeType
  - normalizePathPattern / globMatch / findUp / up / globUp
  - DirEntry {name, type}
  - 文件操作: isDir / isFile / exists / readFileString / readDirectoryEntries /
             ensureDir / writeWithDirs / readJson / writeJson

与 core.py 的关系：core.py 提供等价路径工具与 ScanCache（历史接口），本模块
以 opencode 命名与语义补充完整 FSUtil 命名空间；文件工具层统一走本模块。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .gitignore import glob_to_regex


class FileSystemError(RuntimeError):
    """平台无关的文件系统错误(glob 语法错、非法模式等)。

    用于区分"文件缺失/IO 故障"(透传 OSError)与"调用参数层面的错误",
    避免上层把逻辑错误误判为文件缺失。对齐规格 §4.2 ③。
    """


@dataclass
class DirEntry:
    """目录条目，对应 opencode DirEntry {name, type}。"""

    name: str
    type: str  # "file" | "dir"

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type}


@dataclass
class FileStat:
    """stat 结果，对齐规格 §4.1 stat() => {size, type, ...}。"""

    size: int
    type: str  # "File" | "Directory" | "Symlink" | "Unknown"
    mtime_ns: int


def normalize_path(path: str | os.PathLike[str]) -> str:
    """规范化路径:展开 ~ 与相对路径,解析符号链接,返回绝对路径字符串。

    对应 opencode normalizePath(realpathSync.native),Windows 下大小写归一。
    """
    p = Path(os.path.expanduser(str(path)))
    try:
        resolved = p.resolve(strict=False)
    except OSError:
        resolved = p.absolute()
    return str(resolved)


def windows_path(path: str) -> str:
    """将 POSIX 风格路径(含 /mnt/c 或 /c/ cygwin 形式)转换为 Windows 路径。

    opencode windowsPath() 语义;非 POSIX 形式直接返回。
    """
    p = path.replace("/", "\\")
    if re.match(r"^[A-Za-z]:", p):
        return p
    m = re.match(r"^\\([a-zA-Z])\\(.*)$", p)
    if m:
        return f"{m.group(1).upper()}:\\{m.group(2)}"
    return p


def contains(parent: str | os.PathLike[str], child: str | os.PathLike[str]) -> bool:
    """child 是否位于 parent 之下(含相等)。opencode contains() 语义。"""
    pp = Path(normalize_path(parent))
    cp = Path(normalize_path(child))
    return cp == pp or cp.is_relative_to(pp)


def overlaps(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """两条路径是否存在包含关系(任一方是另一方的父路径,含相等)。"""
    pa = Path(normalize_path(a))
    pb = Path(normalize_path(b))
    return pa == pb or pa.is_relative_to(pb) or pb.is_relative_to(pa)


def resolve(path: str | os.PathLike[str]) -> str:
    """解析路径为绝对路径;无法解析时回退到绝对化结果。"""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def mime_type(path: str | os.PathLike[str]) -> str:
    """按扩展名猜测 MIME 类型,对应 opencode mimeType()。"""
    _MIME_MAP = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".pdf": "application/pdf",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".flac": "audio/flac", ".m4a": "audio/mp4",
        ".mp4": "video/mp4", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
        ".mkv": "video/x-matroska", ".webm": "video/webm",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json",
        ".csv": "text/csv", ".xml": "application/xml", ".yaml": "text/yaml",
        ".yml": "text/yaml", ".log": "text/plain",
        ".py": "text/x-python", ".js": "text/javascript", ".ts": "text/typescript",
        ".jsx": "text/jsx", ".tsx": "text/tsx", ".vue": "text/x-vue",
        ".html": "text/html", ".css": "text/css", ".scss": "text/x-scss",
        ".sh": "text/x-sh", ".bat": "text/x-batch", ".ps1": "text/x-powershell",
        ".toml": "application/toml", ".sql": "text/x-sql", ".ipynb": "application/json",
    }
    ext = Path(path).suffix.lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


def normalize_path_pattern(pattern: str) -> str:
    """规范化路径模式:统一分隔符、去尾部斜杠。opencode normalizePathPattern()。"""
    p = pattern.replace("\\", "/").rstrip("/")
    if p == ".":
        return p
    return p


def glob_match(pattern: str, filepath: str) -> bool:
    """判断 filepath(绝对路径)是否匹配 glob 模式。opencode globMatch() 语义。"""
    p = normalize_path(filepath)
    rx = glob_to_regex(pattern)
    if rx.match(p):
        return True
    # 也允许相对 pattern 匹配相对路径
    rel = p.lstrip("/\\")
    if rx.match(rel):
        return True
    return False


def find_up(names: str | Iterable[str], start: str | os.PathLike[str] | None = None) -> Optional[str]:
    """从 start(默认 cwd)向上逐级查找名为 names 中任意一项的目录/文件。"""
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


def up(path: str | os.PathLike[str], levels: int = 1) -> str:
    """向上取 levels 级父目录。"""
    p = Path(normalize_path(path))
    for _ in range(max(0, levels)):
        p = p.parent
    return str(p)


def glob_up(pattern: str, start: str | os.PathLike[str] | None = None, max_levels: int = 16) -> Iterable[str]:
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


# ── 文件系统操作 ──────────────────────────────────────────────────────────

def is_dir(path: str | os.PathLike[str]) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def is_file(path: str | os.PathLike[str]) -> bool:
    try:
        return Path(path).is_file()
    except OSError:
        return False


def exists(path: str | os.PathLike[str]) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return False


def read_file_string(path: str | os.PathLike[str], encoding: str = "utf-8", errors: str = "replace") -> str:
    """读取文本文件内容;编码失败回退到 errors=replace。"""
    with open(path, "r", encoding=encoding, errors=errors) as f:
        return f.read()


def read_file(path: str | os.PathLike[str]) -> bytes:
    """读取文件原始字节,对应规格 §4.1 readFile(path)。"""
    with open(path, "rb") as f:
        return f.read()


def stat(path: str | os.PathLike[str]) -> Optional[FileStat]:
    """返回路径 stat 信息;不存在返回 None。对应规格 §4.1 stat(path)。"""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    if p.is_symlink():
        t = "Symlink"
    elif p.is_dir():
        t = "Directory"
    elif p.is_file():
        t = "File"
    else:
        t = "Unknown"
    return FileStat(size=st.st_size, type=t, mtime_ns=st.st_mtime_ns)


def _atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """原子写:先写 `path.tmp-<rand>` 同目录临时文件再 os.replace。

    对齐规格 §4.2 ① —— 写路径不被并发读者看到半个文件,失败不留半成品。
    """
    p = Path(path)
    tmp = p.parent / f".{p.name}.tmp-{secrets.token_hex(8)}"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_file(path: str | os.PathLike[str], data: str | bytes, atomic: bool = True) -> None:
    """写入文件(自动建父目录),默认原子写。对应规格 §4.1 writeFile(path, data)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    if atomic:
        _atomic_write_bytes(p, payload)
    else:
        p.write_bytes(payload)


def append_file(path: str | os.PathLike[str], data: str | bytes) -> None:
    """追加内容到文件;文件不存在时原子创建。对应规格 §4.1 appendFile(path, data)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    if p.exists():
        with open(p, "ab") as f:
            f.write(payload)
    else:
        _atomic_write_bytes(p, payload)


def remove(path: str | os.PathLike[str]) -> None:
    """删除文件或目录(目录递归),不存在时静默成功。对应规格 §4.1 remove(path)。"""
    p = Path(path)
    try:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def read_directory_entries(path: str | os.PathLike[str]) -> list[DirEntry]:
    """列出目录下的条目(仅 name/type,不 stat),对应 opencode readDir()。"""
    entries: list[DirEntry] = []
    try:
        for child in sorted(Path(path).iterdir(), key=lambda p: p.name.lower()):
            try:
                entries.append(DirEntry(name=child.name, type="dir" if child.is_dir() else "file"))
            except OSError:
                continue
    except OSError:
        pass
    return entries


def ensure_dir(path: str | os.PathLike[str]) -> None:
    """确保目录存在(含父级),对应 opencode ensureDir()。"""
    Path(path).mkdir(parents=True, exist_ok=True)


def write_with_dirs(path: str | os.PathLike[str], content: str | bytes) -> None:
    """写入文件并自动创建父目录,对应 opencode writeWithDirs()。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8", newline="")


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    """读取 JSON 文件;不存在或解析失败返回 default。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str | os.PathLike[str], data: Any) -> None:
    """写 JSON 文件(自动建目录,原子替换)。"""
    write_file(path, json.dumps(data, ensure_ascii=False, indent=2))


_FILE_LOCKS: dict[str, tuple[threading.RLock, int]] = {}
_FILE_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def file_lock(path: str | os.PathLike[str], *, timeout: Optional[float] = None) -> Iterator[None]:
    """文件级可重入锁(按规范化绝对路径,Windows 下大小写不敏感)。

    对齐规格 §4.2 ② —— 对同一文件的编辑必须串行化,防止并发的部分写。
    用法:
        with file_lock("/workspace/a/b.txt"):
            ...
    同一路径可重入;并发请求不同路径互不阻塞。锁注册表自动清理。
    """
    key = normalize_path(path)
    if os.name == "nt":
        key = key.lower()
    with _FILE_LOCKS_GUARD:
        lock, count = _FILE_LOCKS.get(key, (threading.RLock(), 0))
        _FILE_LOCKS[key] = (lock, count + 1)
    if timeout is None:
        lock.acquire()
    elif not lock.acquire(timeout=timeout):
        with _FILE_LOCKS_GUARD:
            _l, c2 = _FILE_LOCKS.get(key, (lock, 1))
            if c2 <= 1:
                _FILE_LOCKS.pop(key, None)
            else:
                _FILE_LOCKS[key] = (_l, c2 - 1)
        raise FileSystemError(f"timeout acquiring file lock for {path}")
    try:
        yield
    finally:
        lock.release()
        with _FILE_LOCKS_GUARD:
            _l, c2 = _FILE_LOCKS.get(key, (lock, 1))
            if c2 <= 1:
                _FILE_LOCKS.pop(key, None)
            else:
                _FILE_LOCKS[key] = (_l, c2 - 1)
