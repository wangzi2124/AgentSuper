"""L5 安全测试：沙箱边界（agent.md §10.7）。

平台聊天经 `execution/filesystem.py:VirtualFileSystem` 沙箱落盘，路径越界一律拒绝。
覆盖：`../` 遍历、绝对路径逃逸、允许根授权的临时放行、允许根复位后的再越界，
以及 `allowed_roots` 的 contextvar 隔离（并发任务不串扰）。
"""

from __future__ import annotations

import asyncio

import pytest

from execution.filesystem import (
    VirtualFileSystem,
    reset_allowed_roots,
    set_allowed_roots,
)


@pytest.fixture
def vfs(tmp_path):
    return VirtualFileSystem(root=str(tmp_path / "sandbox"))


def test_relative_traversal_blocked(vfs):
    with pytest.raises(PermissionError):
        vfs._resolve("../../etc/passwd")


def test_absolute_escape_blocked(vfs):
    with pytest.raises(PermissionError):
        vfs.write_file("C:\\Windows\\System32\\drivers\\etc\\hosts", "x")


def test_read_traversal_blocked(vfs):
    with pytest.raises(PermissionError):
        vfs.read_file("../../../secret.txt")


def test_is_allowed_reflects_bounds(vfs, tmp_path):
    inside = "a.txt"
    outside = str(tmp_path / "outside" / "b.txt")
    assert vfs.is_allowed(inside) is True
    assert vfs.is_allowed(outside) is False


def test_allowed_root_grants_then_reverts(vfs, tmp_path):
    ext = tmp_path / "ext"
    ext.mkdir()
    target = str(ext / "a.txt")

    with pytest.raises(PermissionError):
        vfs.write_file(target, "x")

    token = set_allowed_roots([str(ext)])
    try:
        assert vfs.is_allowed(target) is True
        vfs.write_file(target, "x")
        assert (ext / "a.txt").read_text(encoding="utf-8") == "x"
    finally:
        reset_allowed_roots(token)

    assert vfs.is_allowed(target) is False
    with pytest.raises(PermissionError):
        vfs.write_file(target, "y")


def test_allowed_roots_context_isolated(tmp_path):
    """同一 VFS 下，授权只作用于设置它的协程上下文。"""
    ext = tmp_path / "ext"
    ext.mkdir()
    vfs = VirtualFileSystem(root=str(tmp_path / "sandbox"))
    target = str(ext / "a.txt")

    async def grant_and_check():
        token = set_allowed_roots([str(ext)])
        try:
            await asyncio.sleep(0)
            return vfs.is_allowed(target)
        finally:
            reset_allowed_roots(token)

    async def deny_check():
        await asyncio.sleep(0)
        return vfs.is_allowed(target)

    async def main():
        return await asyncio.gather(grant_and_check(), deny_check())

    granted, denied = asyncio.run(main())
    assert granted is True
    assert denied is False
