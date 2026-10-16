"""快照与回滚层。

合并两套快照能力：
- 内部 git 仓库的每步文件快照（`Snapshot`/`Patch`/`diff_turn`/`restore_session_turn`，
  规格模块 B）——见 `snapshot` / `turn` 子模块。
- 平台轮次快照（`snapshot_begin`/`snapshot_end`/`restore`/`render_diff`，对齐前端
  `restore-snapshot` / `diff` 契约）——基于 `VirtualFileSystem` 的写入历史，
  见 `vfs` 子模块。
"""
from __future__ import annotations

from .snapshot import (
    DEFAULT_BIG_FILE_THRESHOLD,
    FileDiff,
    Patch,
    Snapshot,
    SnapshotError,
)
from .turn import (
    cleanup_turn_archives,
    diff_turn,
    restore_session_turn,
)
from .vfs import render_diff, restore, snapshot_begin, snapshot_end

__all__ = [
    "DEFAULT_BIG_FILE_THRESHOLD",
    "FileDiff",
    "Patch",
    "Snapshot",
    "SnapshotError",
    "diff_turn",
    "restore_session_turn",
    "cleanup_turn_archives",
    "snapshot_begin",
    "snapshot_end",
    "restore",
    "render_diff",
]
