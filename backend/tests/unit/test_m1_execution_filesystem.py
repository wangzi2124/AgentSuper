"""L1 单元测试：VirtualFileSystem（agent.md §10.2 / §5.1）。"""

import pytest

from execution.filesystem import (
    VirtualFileSystem,
    bounded,
    reset_allowed_roots,
    set_allowed_roots,
)


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
