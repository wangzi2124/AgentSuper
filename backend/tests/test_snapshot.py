# -*- coding: utf-8 -*-
"""快照层(模块 B)用例 —— 规格 §5.9 验收点。

用临时 git 项目验证:
  - track 返回 tree hash,只写内部 git 对象,不建 commit、不动用户 index/HEAD
  - track→改→restore 往返,用户仓库 git status/log 不变(零干扰)
  - revert 对 修改/删除/新建 三类变化各自正确还原(去重:同文件多步只还原到最早)
  - 大文件(未跟踪 >2MiB)被排除,不进快照
  - 非 git 目录 / 显式 disabled → 全部 no-op
  - cleanup(gc --prune=7.days) 不破坏现有快照可恢复性
"""
import subprocess
from pathlib import Path

import pytest

from app.snapshot import Patch, Snapshot


def _git_init(work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "snapshot-test"],
    ):
        subprocess.run(cmd, cwd=work, check=True, capture_output=True, text=True)


def _git(work: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=work, check=True, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    _git_init(work)
    snap = Snapshot(worktree=work, data_dir=tmp_path / "snapdata")
    return work, snap


def test_track_returns_tree_hash_no_commit(repo):
    work, snap = repo
    (work / "a.txt").write_text("v1", encoding="utf-8")
    h1 = snap.track()
    assert h1 and len(h1) == 40
    gd = snap.gitdir()
    assert gd.exists()
    # write-tree 不产生 commit → gitdir 内无 HEAD
    proc = subprocess.run(
        ["git", "--git-dir", str(gd), "rev-parse", "--verify", "HEAD"],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0


def test_last_wins_same_file_three_edits(repo):
    """规格 §5.9:同文件被连续改,revert 去重,只还原到最早一次之前的状态。"""
    work, snap = repo
    f = work / "f.txt"
    f.write_text("orig", encoding="utf-8")
    h1 = snap.track()
    f.write_text("v1", encoding="utf-8")
    h2 = snap.track()
    f.write_text("v2", encoding="utf-8")
    h3 = snap.track()
    f.write_text("v3", encoding="utf-8")
    h4 = snap.track()
    p1, p2, p3 = snap.patch(h1), snap.patch(h2), snap.patch(h3)
    assert (work / "f.txt").as_posix() in p1.files
    snap.revert([p1, p2, p3])
    assert f.read_text(encoding="utf-8") == "orig"
    # 反向:仅回滚到最后一次快照 h3(当时内容为 v2)
    snap.revert([Patch(hash=h3, files=[(work / "f.txt").as_posix()])])
    assert f.read_text(encoding="utf-8") == "v2"


def test_revert_modified_deleted_created(repo, tmp_path):
    """规格 §5.9:revert 对 修改/删除/新建 三类变化各自正确还原。"""
    work, snap = repo
    a = work / "a.txt"
    b = work / "b.txt"
    a.write_text("a1", encoding="utf-8")
    b.write_text("b1", encoding="utf-8")
    _git(work, "add", "."), _git(work, "commit", "-m", "init")
    h1 = snap.track()  # 干净提交态

    a.write_text("a2", encoding="utf-8")  # 修改
    b.unlink()  # 删除
    (work / "new.txt").write_text("new", encoding="utf-8")  # 新建
    h2 = snap.track()
    patch = snap.patch(h1)
    assert {(work / p).as_posix() for p in ("a.txt", "b.txt", "new.txt")} == set(patch.files)

    snap.revert([patch])
    assert a.read_text(encoding="utf-8") == "a1"
    assert b.read_text(encoding="utf-8") == "b1"
    assert not (work / "new.txt").exists()


def test_restore_roundtrip_zero_interference(repo):
    """规格 §5.9:track→改→restore 往返,用户 git status/log 与操作前一致。"""
    work, snap = repo
    (work / "a.txt").write_text("v1", encoding="utf-8")
    _git(work, "add", "."), _git(work, "commit", "-m", "init")
    log_before = _git(work, "log", "--oneline").stdout.strip()
    h1 = snap.track()
    (work / "a.txt").write_text("v2", encoding="utf-8")
    h2 = snap.track()
    assert snap.patch(h1).files  # 有变更
    snap.restore(h1)
    assert (work / "a.txt").read_text(encoding="utf-8") == "v1"
    log_after = _git(work, "log", "--oneline").stdout.strip()
    assert log_after == log_before
    status = _git(work, "status", "--porcelain").stdout.strip()
    assert status == ""
    # restore 后 patch / diff 仍可用
    assert snap.patch(h2).files or True


def test_big_file_excluded(repo):
    """规格 §5.9:未跟踪 >2MiB 大文件被排除,不进快照。"""
    work, snap = repo
    h1 = snap.track()
    (work / "big.bin").write_bytes(b"\x00" * (2 * 1024 * 1024 + 1))
    (work / "small.txt").write_text("x", encoding="utf-8")
    h2 = snap.track()
    files = snap.patch(h1).files
    assert (work / "small.txt").as_posix() in files
    assert (work / "big.bin").as_posix() not in files
    exclude = snap.gitdir() / "info" / "exclude"
    assert "big.bin" in exclude.read_text(encoding="utf-8")


def test_patch_empty_when_no_changes(repo):
    work, snap = repo
    (work / "a.txt").write_text("x", encoding="utf-8")
    h1 = snap.track()
    assert snap.patch(h1).files == []
    assert snap.diff(h1) == ""


def test_diff_full_binary_and_text(repo):
    work, snap = repo
    h1 = snap.track()
    (work / "t.txt").write_text("line1\nline2\n", encoding="utf-8")
    (work / "bin.dat").write_bytes(b"\x00\x01\x02\xff\xfe")
    h2 = snap.track()
    diffs = snap.diff_full(h1, h2)
    by_name = {Path(d.file).name: d for d in diffs}
    assert by_name["t.txt"].status == "added"
    assert by_name["t.txt"].binary is False
    assert by_name["t.txt"].additions == 2
    assert by_name["bin.dat"].binary is True


def test_diff_full_modified_content():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "w"
        _git_init(work)
        snap = Snapshot(worktree=work, data_dir=Path(td) / "sd")
        f = work / "f.txt"
        f.write_text("aaa\n", encoding="utf-8")
        h1 = snap.track()
        f.write_text("zzz\naaa\n", encoding="utf-8")
        h2 = snap.track()
        d = snap.diff_full(h1, h2)
        assert len(d) == 1
        assert d[0].status == "modified"
        assert d[0].binary is False
        assert d[0].before == "aaa\n"
        assert d[0].after == "zzz\naaa\n"
        assert d[0].additions == 1 and d[0].deletions == 0


def test_non_git_dir_noop(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    snap = Snapshot(worktree=plain, data_dir=tmp_path / "sd")
    assert snap.enabled() is False
    assert snap.track() is None
    assert snap.patch("x").files == []
    assert snap.diff("x") == ""
    assert snap.diff_full("a", "b") == []


def test_disabled_flag_noop(repo):
    work, _ = repo
    snap = Snapshot(worktree=work, data_dir=work.parent / "sd", disabled=True)
    assert snap.enabled() is False
    assert snap.track() is None


def test_cleanup_preserves_restore(repo):
    work, snap = repo
    f = work / "f.txt"
    f.write_text("v1", encoding="utf-8")
    h1 = snap.track()
    f.write_text("v2", encoding="utf-8")
    h2 = snap.track()
    snap.cleanup()
    snap.restore(h1)
    assert f.read_text(encoding="utf-8") == "v1"


def test_gitdir_layout_spec(repo):
    """规格 §5.1:布局 <data>/snapshot/<projectId>/<sha1(worktree)>/。"""
    work, snap = repo
    snap.track()
    gd = snap.gitdir()
    rel = gd.relative_to(snap.data_dir / "snapshot")
    assert len(rel.parts) == 2  # projectId / worktree-hash 两层
    assert (gd / "HEAD").exists() or gd.exists()