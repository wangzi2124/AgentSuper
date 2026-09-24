"""
快照与回滚层(规格模块 B)。

基于内部 git 仓库的每步文件快照:
  - Snapshot.track()       拍当前工作区快照,返回 tree hash(零干扰,不建 commit)
  - Snapshot.patch(hash)   自某快照后的变更文件清单
  - Snapshot.diff(hash)    自某快照后的 diff 文本
  - Snapshot.diff_full()   每文件 before/after / 增减行 / 二进制标记
  - Snapshot.restore(hash) 整仓回滚
  - Snapshot.revert(patches) 按文件精确回滚
  - Snapshot.cleanup()     git gc --prune=7.days

典型用法:
    snap = Snapshot(worktree="repo/", data_dir="backend/data")
    h1 = snap.track()            # 步骤前
    ... 修改文件 ...
    h2 = snap.track()            # 步骤后
    p = snap.patch(h1)           # 本步骤 changed files
    snap.revert([p])             # 文件级回滚到本步之前
"""
from __future__ import annotations

from .snapshot import (
    DEFAULT_BIG_FILE_THRESHOLD,
    FileDiff,
    Patch,
    Snapshot,
    SnapshotError,
)

__all__ = [
    "DEFAULT_BIG_FILE_THRESHOLD",
    "FileDiff",
    "Patch",
    "Snapshot",
    "SnapshotError",
]