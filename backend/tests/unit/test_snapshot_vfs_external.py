"""轮次快照 / 回滚对「已审批外部路径」的容错（回归）。

现场 `ses_e0013e2e18c14793`：用户审批后 `write_file` 把 C:/tool/test.txt 成功落盘，
但回合末 `snapshot_end` 在**请求级上下文**里对同一路径再走一次沙箱校验 → `PermissionError:
路径越界` → SSE 以 error 收尾（文件其实已写入）。

根因：审批发生在图任务上下文，`add_allowed_root` 只就地更新该上下文的 contextvar，
不向上传播到请求级；而 `_history` / 快照/回滚只可能涉及**当次已通过写入校验**的路径，
不该二次判定边界。释放方式 = `VirtualFileSystem.raw_path`（不做边界校验）。
"""

from __future__ import annotations

import pytest

from app.snapshot import render_diff, restore, snapshot_begin, snapshot_end
from execution.filesystem import (
    VirtualFileSystem,
    reset_allowed_roots,
    set_allowed_roots,
)


def _approved_external_write(tmp_path):
    """模拟「图任务内审批授权 → 写外部文件 → 授权随上下文消失」。"""
    vfs = VirtualFileSystem(root=str(tmp_path / "sandbox"))
    ext = tmp_path / "ext"
    ext.mkdir()
    target = str(ext / "test.txt")
    begin = snapshot_begin(vfs)
    token = set_allowed_roots([str(ext)])
    try:
        vfs.write_file(target, "hello")
    finally:
        reset_allowed_roots(token)
    return vfs, ext, target, begin


def test_snapshot_end_after_external_write_does_not_raise(tmp_path):
    vfs, ext, target, begin = _approved_external_write(tmp_path)

    # 请求级上下文已无该授权 → 普通读写仍严格越界（安全边界不变）
    with pytest.raises(PermissionError):
        vfs.read_file(target)

    files_changed, descriptor = snapshot_end(vfs, begin)
    assert [f["file"] for f in files_changed] == [target]
    assert files_changed[0]["status"] == "added"
    assert files_changed[0]["additions"] == 1
    assert descriptor["files"] == [{"path": target, "old": None}]


def test_snapshot_diff_and_restore_cover_external_path(tmp_path):
    vfs, ext, target, begin = _approved_external_write(tmp_path)
    _, descriptor = snapshot_end(vfs, begin)

    diff = render_diff(vfs, descriptor)
    assert diff["files"] and diff["files"][0]["file"] == target
    assert "+hello" in diff["files"][0]["diff"]

    restore(vfs, descriptor)
    assert not (ext / "test.txt").exists()


def test_rollback_covers_external_write(tmp_path):
    vfs, ext, target, _ = _approved_external_write(tmp_path)
    assert (ext / "test.txt").exists()

    assert vfs.rollback(1) == 1
    assert not (ext / "test.txt").exists()
