"""L1 单元测试：VirtualFileSystem（agent.md §10.2 / §5.1）。"""

import os

import pytest

from execution.filesystem import (
    VirtualFileSystem,
    bounded,
    normalize_tool_path,
    reset_allowed_roots,
    set_allowed_roots,
)
from execution.tools.file_tools import glob_files


@pytest.fixture
def vfs(tmp_path):
    return VirtualFileSystem(root=str(tmp_path))


def test_allowed_roots_permit_external_path(tmp_path):
    root = tmp_path / "root"
    ext = tmp_path / "ext"
    root.mkdir()
    ext.mkdir()
    vfs = VirtualFileSystem(root=str(root))
    target = str(ext / "a.txt")

    # 未授权：越界拒绝
    with pytest.raises(PermissionError):
        vfs.write_file(target, "x")
    assert vfs.is_allowed(target) is False

    token = set_allowed_roots([str(ext)])
    try:
        assert vfs.is_allowed(target) is True
        vfs.write_file(target, "x")
        assert (ext / "a.txt").read_text(encoding="utf-8") == "x"
    finally:
        reset_allowed_roots(token)

    # 复位后重新越界
    assert vfs.is_allowed(target) is False
    with pytest.raises(PermissionError):
        vfs.write_file(target, "y")


def test_path_traversal_blocked(vfs):
    with pytest.raises(PermissionError):
        vfs._resolve("../../etc/passwd")


def test_write_then_read(vfs):
    vfs.write_file("a/b.txt", "hello")
    assert vfs.read_file("a/b.txt") == "hello"


def test_edit_requires_exact_match(vfs):
    vfs.write_file("a.txt", "foo bar")
    assert "错误" in vfs.edit_file("a.txt", "foo  bar", "x")


def test_edit_replaces_first_match(vfs):
    vfs.write_file("a.txt", "foo bar foo")
    vfs.edit_file("a.txt", "foo", "baz")
    assert vfs.read_file("a.txt") == "baz bar foo"


def test_ls_lists_dirs_with_slash(vfs):
    vfs.write_file("d/f.txt", "x")
    listing = vfs.ls(".")
    assert "d/" in listing


def test_grep_finds_matches(vfs):
    vfs.write_file("a.txt", "alpha\nbeta\ngamma")
    out = vfs.grep("a", ".")
    assert "a.txt:2: beta" in out or "a.txt:3: gamma" in out


def test_rollback_restores(vfs):
    vfs.write_file("a.txt", "v1")
    vfs.write_file("a.txt", "v2")
    vfs.rollback(1)
    assert vfs.read_file("a.txt") == "v1"


def test_rollback_removes_created_file(vfs, tmp_path):
    vfs.write_file("new.txt", "x")
    vfs.rollback(1)
    assert not (tmp_path / "new.txt").exists()


def test_output_truncated(vfs):
    vfs.write_file("big.txt", "x" * 100_000)
    result = vfs.read_file("big.txt")
    assert "已省略" in result
    assert len(result) < 60_000


def test_bounded_keeps_head_and_tail():
    text = "A" * 10 + "B" * 1000 + "C" * 10
    out = bounded(text, limit=100)
    assert out.startswith("A")
    assert out.endswith("C")
    assert "已省略" in out


@pytest.mark.skipif(os.name != "nt", reason="MSYS drive paths are Windows-only")
def test_normalize_tool_path_msys_drive():
    assert normalize_tool_path("/e/") == "E:\\"
    assert normalize_tool_path("/e/foo/bar.txt") == r"E:\foo\bar.txt"
    assert normalize_tool_path("e:") == "E:\\"
    assert normalize_tool_path("E:\\foo") == "E:\\foo"


@pytest.mark.skipif(os.name != "nt", reason="MSYS drive paths are Windows-only")
def test_file_tools_accept_msys_drive_path(vfs, tmp_path):
    vfs.write_file("a.txt", "hello")
    posix = tmp_path.as_posix()
    msys_root = f"/{posix[0].lower()}{posix[2:]}"

    assert vfs.is_allowed(f"{msys_root}/a.txt") is True
    assert vfs.ls(msys_root) == "a.txt"
    assert vfs.read_file(f"{msys_root}/a.txt") == "hello"
    assert "a.txt" in vfs.grep("hello", msys_root)
    assert glob_files(vfs, "*.txt", msys_root) == "a.txt"

    vfs.write_file(f"{msys_root}/b.txt", "created")
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "created"
    vfs.rollback(1)
    assert not (tmp_path / "b.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="MSYS drive paths are Windows-only")
def test_permission_abs_path_normalizes_msys_drive(vfs, tmp_path):
    from app.deps import PlatformContext

    posix = tmp_path.as_posix()
    msys_root = f"/{posix[0].lower()}{posix[2:]}"

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.vfs = vfs
    assert PlatformContext._abs_for_permission(ctx, msys_root) == tmp_path.resolve()
