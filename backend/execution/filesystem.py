"""虚拟文件系统（对齐 agent.md §5.1）。

Agent 的读写操作映射到隔离工作目录：
- 路径沙箱（防路径穿越）
- 变更历史（可回滚）
- 有界输出（头尾截断，见 §7.6.5）
"""

from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MAX_OUTPUT_CHARS = 50_000
GREP_MAX_RESULTS = 100

# 本回合允许的额外根目录（对齐 opencode `ctx.directory` / 前端「工作目录」面板）。
# 由 API 层按会话注入（会话目录 + 用户工作目录），随 contextvar 传播到图节点任务；
# 沙箱默认根（`VirtualFileSystem.root`）始终允许。审批通过的外部路径也并入此处。
_allowed_roots_var: contextvars.ContextVar[tuple[Path, ...]] = contextvars.ContextVar(
    "vfs_allowed_roots", default=()
)


def set_allowed_roots(roots: Iterable[str]) -> contextvars.Token:
    """设置本回合允许的额外根（工作目录 / 会话目录）；返回 token 供复位。"""
    norm: list[Path] = []
    for item in roots:
        if not item:
            continue
        try:
            norm.append(Path(item).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    return _allowed_roots_var.set(tuple(norm))


def reset_allowed_roots(token: contextvars.Token) -> None:
    _allowed_roots_var.reset(token)


def current_allowed_roots() -> tuple[Path, ...]:
    return _allowed_roots_var.get()


def add_allowed_root(path: str) -> None:
    """把单个路径（审批通过后）并入本回合允许根（就地更新，回合结束随 reset 清理）。"""
    if not path:
        return
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return
    roots = _allowed_roots_var.get()
    if p not in roots:
        _allowed_roots_var.set((*roots, p))


def bounded(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """头尾截断（对齐 agent.md §7.6.5）：同时保留头尾，避免丢命令行或错误堆栈末尾。"""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n... [中间 {len(text) - limit} 字符已省略] ...\n\n{tail}"


@dataclass
class _HistoryEntry:
    path: str
    old: str | None


class VirtualFileSystem:
    """隔离工作目录内的读写/编辑/列目录/搜索。"""

    def __init__(self, root: str = "./workspace") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._history: list[_HistoryEntry] = []

    def _resolve(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if not self._within(p):
            raise PermissionError(f"路径越界: {path}")
        return p

    @staticmethod
    def _under(p: Path, root: Path) -> bool:
        return p == root or root in p.parents

    def _within(self, p: Path) -> bool:
        """路径是否落在沙箱根或任一允许根内。"""
        if self._under(p, self.root):
            return True
        return any(self._under(p, r) for r in _allowed_roots_var.get())

    def is_allowed(self, path: str) -> bool:
        """判断路径是否在沙箱/允许根内（供审批门控：在内则免审批，对齐 external ask 语义）。"""
        try:
            p = (self.root / path).resolve()
        except (OSError, RuntimeError):
            return False
        return self._within(p)

    def read_file(self, path: str) -> str:
        fp = self._resolve(path)
        if not fp.exists():
            return f"错误：文件不存在 {path}"
        if fp.is_dir():
            return f"错误：{path} 是目录，请用 ls"
        content = fp.read_text(encoding="utf-8", errors="replace")
        return bounded(content)

    def write_file(self, path: str, content: str) -> str:
        fp = self._resolve(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        old = fp.read_text(encoding="utf-8") if fp.exists() else None
        self._history.append(_HistoryEntry(path, old))
        fp.write_text(content, encoding="utf-8")
        return f"已写入 {len(content)} 字符到 {path}"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        fp = self._resolve(path)
        if not fp.exists():
            return f"错误：文件不存在 {path}"
        content = fp.read_text(encoding="utf-8")
        if old_text not in content:
            return "错误：未找到匹配文本，请检查空白字符"
        self._history.append(_HistoryEntry(path, content))
        fp.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"已编辑 {path}"

    def ls(self, path: str = ".") -> str:
        dp = self._resolve(path)
        if not dp.is_dir():
            return f"错误：{path} 不是目录"
        return "\n".join(
            f"{e.name}/" if e.is_dir() else e.name
            for e in sorted(dp.iterdir(), key=lambda p: p.name)
        )

    def grep(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"错误：正则表达式无效：{e}"
        base = self._resolve(path)
        results: list[str] = []
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(
                    f.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if rx.search(line):
                        results.append(
                            f"{f.relative_to(self.root)}:{i}: {line.strip()}"
                        )
                        if len(results) >= GREP_MAX_RESULTS:
                            return "\n".join(results) + "\n... (已截断)"
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(results) or "无匹配结果"

    def rollback(self, steps: int = 1) -> int:
        """回滚文件变更，返回实际回滚条数。"""
        count = 0
        for entry in reversed(self._history[-steps:]):
            fp = self._resolve(entry.path)
            if entry.old is not None:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(entry.old, encoding="utf-8")
            elif fp.exists():
                fp.unlink()
            count += 1
        del self._history[-steps:]
        return count

    def tools(self) -> list[tuple[str, str, object]]:
        """返回 (name, description, bound_method) 列表，供 ToolRegistry 注册。"""
        return [
            ("read_file", self.read_file.__doc__ or "读取文本文件内容。", self.read_file),
            (
                "write_file",
                self.write_file.__doc__ or "写入文件（覆盖或新建）。",
                self.write_file,
            ),
            (
                "edit_file",
                self.edit_file.__doc__ or "在文件中精确替换文本。",
                self.edit_file,
            ),
            ("ls", self.ls.__doc__ or "列出目录内容。", self.ls),
            ("grep", self.grep.__doc__ or "用正则递归搜索文件内容。", self.grep),
        ]
