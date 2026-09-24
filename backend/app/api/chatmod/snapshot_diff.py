"""chat 轮次内文件快照 diff（对接 `/api/chat/multi-agent*` 的调试点）。

在每次用户请求前开启轮次级 turn 并拍 before tree、请求完成后合并 git diff 与
外部文件归档结果，得到「本次改动哪些文件 + 增减行数 + 恢复描述」：
- `_before_hash(request)` 开 turn、拍 before tree，返回 tree hash（不可用返回 ''）；
- `_files_changed(request, before)` 收尾：返回 (files_changed, restore descriptor)，
  并在 finally 中结束当前 turn（contextvar 清空，防串扰）。
- `_abort_turn()` 供端点错误路径调用，防止 turn 泄漏到下一个请求。

自动降级：snapshot 未接线 / 非 git 项目 / 出错 → 返回 (空, {})，绝不影响聊天主路径。
外部文件（worktree 之外，如用户桌面路径）即使项目非 git，也照样归档、可恢复。
"""

import logging
import os

from app.snapshot.turn import (
    active_turn as _active_turn,
    end_turn as _end_turn,
    start_turn as _start_turn,
)

logger = logging.getLogger(__name__)


def _before_hash(request) -> str:
    """请求开始前开启轮次级 turn 并拍 before tree，返回 tree hash；不可用/失败返回 ''。"""
    snap = getattr(request.app.state, "snapshot", None)
    if snap is None:
        return ""
    try:
        _start_turn(snap)
        turn = _active_turn()
        return (turn.capture_before() if turn is not None else "")
    except Exception:
        logger.warning("snapshot before-track failed", exc_info=True)
        _end_turn()
        return ""


def _files_changed(request, before: str) -> tuple[list, dict]:
    """对比 before → 当前工作区（git 内部）+ 外部归档，返回 (files_changed, descriptor)。

    file 归一为 worktree 相对路径（内部文件）；外部文件保留绝对路径并标 external=True。
    """
    snap = getattr(request.app.state, "snapshot", None)
    turn = _active_turn()
    if snap is None or turn is None:
        return [], {}
    try:
        git_entries: list[dict] = []
        if before and snap.enabled():
            after = snap.track()
            if after and after != before:
                worktree = os.fspath(snap.worktree)
                for d in snap.diff_full(before, after):
                    rel = d.file
                    if worktree:
                        try:
                            rel = os.path.relpath(d.file, worktree)
                        except (OSError, ValueError):
                            pass
                    git_entries.append({
                        "file": rel,
                        "status": d.status,
                        "binary": bool(d.binary),
                        "additions": int(d.additions),
                        "deletions": int(d.deletions),
                    })
        return turn.finalize(git_entries)
    except Exception:
        logger.warning("snapshot files-changed diff failed", exc_info=True)
        return [], {}
    finally:
        _end_turn()


def _abort_turn() -> None:
    _end_turn()