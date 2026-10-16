"""平台轮次快照（对齐前端 `restore-snapshot` / `diff` 契约）。

平台主图通过 `VirtualFileSystem` 读写工作区文件，VFS 的 `_history` 记录每次写入的
`(path, old)`。据此可在「一轮问答」前后界定本轮的文件改动：

- `snapshot_begin(vfs)` → 记录当前 history 长度（轮次起点）
- `snapshot_end(vfs, begin)` → 汇总本轮 files_changed + 一份 **restore descriptor**
  （每个改动路径的「改动前内容」，新建则为 None）
- `restore(vfs, descriptor)` → 把改动路径还原到 descriptor 记录的旧内容（原先不存在则删）
- `render_diff(vfs, descriptor, file=None)` → 生成统一 diff 文本

纯函数、无副作用（除 restore 写盘），便于单测。
"""

from __future__ import annotations

import difflib
from typing import Any

from execution.filesystem import VirtualFileSystem

_DIFF_MAX_BYTES = 200_000


def snapshot_begin(vfs: VirtualFileSystem) -> int:
    """返回轮次起点（当前变更历史长度）。"""
    return len(getattr(vfs, "_history", []))


def _read(vfs: VirtualFileSystem, path: str) -> str | None:
    fp = vfs._resolve(path)  # noqa: SLF001 - 同平台内部使用
    if not fp.exists() or fp.is_dir():
        return None
    return fp.read_text(encoding="utf-8", errors="replace")


def _line_counts(old: str | None, new: str | None) -> tuple[int, int]:
    old_lines = old.splitlines() if old else []
    new_lines = new.splitlines() if new else []
    adds = dels = 0
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            adds += j2 - j1
        if tag in ("replace", "delete"):
            dels += i2 - i1
    return adds, dels


def snapshot_end(
    vfs: VirtualFileSystem, begin: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """汇总 [begin, now) 的本轮改动 → (files_changed, descriptor)。"""
    history = getattr(vfs, "_history", [])[begin:]
    before: dict[str, str | None] = {}
    order: list[str] = []
    for entry in history:
        if entry.path not in before:
            before[entry.path] = entry.old
            order.append(entry.path)

    files_changed: list[dict[str, Any]] = []
    desc_files: list[dict[str, Any]] = []
    for path in order:
        old = before[path]
        new = _read(vfs, path)
        desc_files.append({"path": path, "old": old})
        if new == old:
            continue
        if old is None:
            status = "added"
        elif new is None:
            status = "deleted"
        else:
            status = "modified"
        adds, dels = _line_counts(old, new)
        files_changed.append(
            {"file": path, "status": status, "additions": adds, "deletions": dels}
        )
    return files_changed, {"files": desc_files, "before_count": len(before)}


def restore(vfs: VirtualFileSystem, descriptor: dict[str, Any]) -> dict[str, Any]:
    """按 descriptor 还原本轮文件改动。"""
    restored: list[str] = []
    for item in descriptor.get("files", []):
        path = item.get("path")
        if not path:
            continue
        fp = vfs._resolve(path)  # noqa: SLF001
        old = item.get("old")
        if old is None:
            if fp.exists():
                fp.unlink()
        else:
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(old, encoding="utf-8")
        restored.append(path)
    return {
        "restored": True,
        "already": False,
        "internal": len(restored),
        "external": 0,
        "restored_files": restored,
        "missing": [],
    }


def render_diff(
    vfs: VirtualFileSystem, descriptor: dict[str, Any], file: str | None = None
) -> dict[str, Any]:
    """生成统一 diff 文本（对齐前端 `fetchTurnDiff` 返回契约）。"""
    files: list[dict[str, str]] = []
    for item in descriptor.get("files", []):
        path = item.get("path")
        if not path:
            continue
        if file and path != file:
            continue
        old = item.get("old") or ""
        new = _read(vfs, path) or ""
        if old == new:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
        if len(diff.encode("utf-8")) > _DIFF_MAX_BYTES:
            diff = diff.encode("utf-8")[:_DIFF_MAX_BYTES].decode("utf-8", "ignore") + "\n... (已截断)"
        files.append({"file": path, "diff": diff})
    return {"files": files, "truncated": False, "reason": "", "steps": 0}


__all__ = ["snapshot_begin", "snapshot_end", "restore", "render_diff"]
