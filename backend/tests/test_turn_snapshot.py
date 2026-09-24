# -*- coding: utf-8 -*-
"""轮次级快照（撤回本轮改动）用例。

覆盖:
  - 外部文件（worktree 之外，如用户桌面路径）写前归档 → 恢复写回
  - turn 前不存在、turn 中新建的外部文件 → 恢复删除
  - git 内部文件：before_tree 整轮恢复
  - worktree 内但被 .gitignore 忽略的文件 → 归档兜底可恢复
  - 同内容写入不产生 external 展示项
  - 非 git 项目：内部 before_tree 为空、外部归档照常工作
  - archive_external 幂等 / 无活跃 turn 时空操作
  - end_turn 清空 contextvar（不泄漏到下一个请求）
  - restore_session_turn 恢复失败单文件降级、descriptor JSON 往返安全
"""
import json
import subprocess
import types
from pathlib import Path

import pytest

from app.api.chatmod import snapshot_diff
from app.snapshot import Snapshot
from app.snapshot.turn import (
    active_turn,
    archive_external,
    cleanup_turn_archives,
    end_turn,
    restore_session_turn,
    start_turn,
)


def _git_init(work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "snapshot-test"],
    ):
        subprocess.run(cmd, cwd=work, check=True, capture_output=True, text=True)


def _request(snap: Snapshot):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(snapshot=snap))
    )


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "work"
    _git_init(work)
    snap = Snapshot(worktree=work, data_dir=tmp_path / "snapdata")
    return work, snap


def _run_turn(repo, mutate):
    """跑一个完整轮次（含上下文任务模拟）：return (files_changed, descriptor)。"""
    work, snap = repo
    request = _request(snap)
    before = snapshot_diff._before_hash(request)
    mutate()
    return snapshot_diff._files_changed(request, before)


# ── 外部文件 ──────────────────────────────────────────────────────────────


def test_external_file_archived_and_restored(repo, tmp_path):
    work, snap = repo
    ext = tmp_path / "outside" / "desktop.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("OLD CONTENT", encoding="utf-8")

    files, desc = _run_turn(
        repo,
        lambda: (archive_external(ext), ext.write_text("NEW CONTENT", encoding="utf-8")),
    )
    # files_changed 含外部文件，且打标 external
    ext_entry = next((e for e in files if e.get("external")), None)
    assert ext_entry is not None
    assert ext_entry["file"] == str(ext.resolve())
    assert ext_entry["status"] == "modified"
    assert desc["external"] and desc["external"][0]["before"] == "present"

    result = restore_session_turn(snap, desc)
    assert ext.read_text(encoding="utf-8") == "OLD CONTENT"
    assert result["external"] == 1


def test_external_created_in_turn_deleted_on_restore(repo, tmp_path):
    work, snap = repo
    ext = tmp_path / "outside" / "new.log"

    def mutate():
        archive_external(ext)  # 不存在 → 记 absent
        ext.parent.mkdir(parents=True, exist_ok=True)
        ext.write_text("created", encoding="utf-8")

    files, desc = _run_turn(repo, mutate)
    entry = next(e for e in files if e.get("external"))
    assert entry["status"] == "added"
    restore_session_turn(snap, desc)
    assert not ext.exists()


def test_external_same_content_not_listed(repo, tmp_path):
    work, snap = repo
    ext = tmp_path / "outside" / "same.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("SAME", encoding="utf-8")

    def mutate():
        archive_external(ext)
        ext.write_text("SAME", encoding="utf-8")  # 内容没变

    files, desc = _run_turn(repo, mutate)
    assert all(not e.get("external") for e in files)
    assert desc["external"] == []


# ── git 内部文件 ──────────────────────────────────────────────────────────


def test_internal_files_restored_via_before_tree(repo, tmp_path):
    work, snap = repo
    f = work / "f.txt"
    f.write_text("orig", encoding="utf-8")

    def mutate():
        f.write_text("v2", encoding="utf-8")
        (work / "new.txt").write_text("added", encoding="utf-8")

    files, desc = _run_turn(repo, mutate)
    names = {e["file"] for e in files}
    assert "f.txt" in names and "new.txt" in names
    assert desc["before_tree"]
    # internal 恢复：f.txt 回到 orig、new.txt 删除
    restore_session_turn(snap, desc)
    assert f.read_text(encoding="utf-8") == "orig"
    assert not (work / "new.txt").exists()


# ── .gitignore 忽略文件 → 归档兜底 ────────────────────────────────────────


def test_gitignored_file_restored_via_archive(repo, tmp_path):
    work, snap = repo
    (work / ".gitignore").write_text("*.log\n", encoding="utf-8")
    ignored = work / "build.log"
    ignored.write_text("OLD", encoding="utf-8")

    def mutate():
        archive_external(ignored)  # worktree 内但被忽略 → 归档兜底
        ignored.write_text("NEW", encoding="utf-8")

    files, desc = _run_turn(repo, mutate)
    entry = next((e for e in files if e.get("external")), None)
    assert entry is not None and entry["file"] == str(ignored.resolve())
    restore_session_turn(snap, desc)
    assert ignored.read_text(encoding="utf-8") == "OLD"


# ── 非 git 项目 ───────────────────────────────────────────────────────────


def test_non_git_project_external_archive_still_works(tmp_path):
    work = tmp_path / "plain"
    work.mkdir(parents=True)
    snap = Snapshot(worktree=work, data_dir=tmp_path / "snapdata")
    assert not snap.enabled()
    ext = tmp_path / "plain_extra" / "f.txt"
    ext.parent.mkdir(parents=True)
    ext.write_text("OLD", encoding="utf-8")

    files, desc = _run_turn(
        _req_and_repo(work, snap),
        lambda: (archive_external(ext), ext.write_text("NEW", encoding="utf-8")),
    )
    # 内部 before_tree 为空；外部仍可恢复
    assert not desc["before_tree"]
    assert any(e.get("external") for e in files)
    restore_session_turn(snap, desc)
    assert ext.read_text(encoding="utf-8") == "OLD"


def _req_and_repo(work, snap):
    # 适配 _run_turn(repo=...) 约定：返回 (work, snap)
    return work, snap


# ── contextvar 生命周期 / 幂等 ─────────────────────────────────────────────


def test_archive_idempotent_and_no_ttl_leak(repo, tmp_path):
    work, snap = repo
    ext = tmp_path / "outside" / "a.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("X", encoding="utf-8")

    with _turn_scope(snap):
        archive_external(ext)
        archive_external(ext)  # 二次归档幂等
        assert len(active_turn().external) == 1
    # scope 结束 → contextvar 清空
    assert active_turn() is None


def _turn_scope(snap):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        start_turn(snap)
        try:
            yield
        finally:
            end_turn()

    return _ctx()


def test_archive_without_active_turn_is_noop(repo, tmp_path):
    work, snap = repo
    ext = tmp_path / "outside" / "b.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("X", encoding="utf-8")
    end_turn()
    archive_external(ext)  # 无活跃 turn → 不应抛异常、不应归档
    assert active_turn() is None


def test_descriptor_json_roundtrip_and_partial_failure(repo, tmp_path):
    work, snap = repo
    ext_ok = tmp_path / "outside" / "ok.py"
    ext_ok.parent.mkdir(parents=True)
    ext_ok.write_text("OLD", encoding="utf-8")
    vanished = tmp_path / "outside" / "gone.py"

    def mutate():
        archive_external(ext_ok)
        ext_ok.write_text("NEW", encoding="utf-8")
        # 另一个外部文件在归档时不存在 → absent 标记
        archive_external(vanished)
        vanished.write_text("data", encoding="utf-8")

    files, desc = _run_turn(repo, mutate)
    # descriptor 可 JSON 序列化（随消息落库）
    roundtrip = json.loads(json.dumps(desc))
    # 人为删除 ok.py 的 blob → 恢复降级：ok.py 保持 NEW，gone.py 删除照常
    blob = list((snap.data_dir / "snapshot" / "turns").rglob("*.bin"))[0]
    blob.unlink()
    result = restore_session_turn(snap, roundtrip)
    assert ext_ok.read_text(encoding="utf-8") == "NEW"  # blob 丢失 → 跳过（降级）
    assert not vanished.exists()  # absent 恢复 → 删除
    assert result["missing"]  # 记录丢失的 blob


def test_cleanup_turn_archives(repo, tmp_path):
    work, snap = repo
    ext = tmp_path / "outside" / "c.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("X", encoding="utf-8")

    def mutate():
        archive_external(ext)
        ext.write_text("Y", encoding="utf-8")

    _run_turn(repo, mutate)
    turns_root = snap.data_dir / "snapshot" / "turns"
    assert turns_root.exists()
    # ttl=0 → 全部清理
    cleanup_turn_archives(ttl_days=0, data_dir=snap.data_dir)
    assert not turns_root.exists() or not list(turns_root.rglob("*.bin"))