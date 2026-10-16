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
    bind_from_payload,
    payload_with_turn,
    reset,
    archive_external,
    cleanup_turn_archives,
    diff_turn,
    end_turn,
    record_write,
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


# ── diff 渲染（聊天 UI「本轮改了什么」可展开）─────────────────────────────


def test_descriptor_carries_after_tree_for_diff(repo):
    """descriptor 必须带 after_tree，否则事后无法渲染 before→after 的 diff。"""
    work, snap = repo

    def mutate():
        (work / "a.txt").write_text("v2\n", encoding="utf-8")

    _files, desc = _run_turn(repo, mutate)
    assert desc["before_tree"]
    assert desc["after_tree"]
    assert desc["after_tree"] != desc["before_tree"]


def test_diff_turn_renders_internal_unified_diff(repo):
    work, snap = repo
    (work / "a.txt").write_text("line1\nline2\n", encoding="utf-8")

    def mutate():
        (work / "a.txt").write_text("line1\nline2-changed\nline3\n", encoding="utf-8")
        (work / "new.txt").write_text("hello\n", encoding="utf-8")

    _files, desc = _run_turn(repo, mutate)
    out = diff_turn(snap, desc)
    assert out["reason"] == ""
    assert out["truncated"] is False
    by_file = {f["file"]: f["diff"] for f in out["files"]}
    assert set(by_file) == {"a.txt", "new.txt"}
    assert "-line2" in by_file["a.txt"]
    assert "+line2-changed" in by_file["a.txt"]
    assert "+line3" in by_file["a.txt"]
    assert "+hello" in by_file["new.txt"]
    # 真正的 unified diff 头
    assert by_file["a.txt"].startswith("diff --git")


def test_diff_turn_single_file_filter(repo):
    work, snap = repo
    (work / "a.txt").write_text("v1\n", encoding="utf-8")
    (work / "b.txt").write_text("v1\n", encoding="utf-8")

    def mutate():
        (work / "a.txt").write_text("v2\n", encoding="utf-8")
        (work / "b.txt").write_text("v2\n", encoding="utf-8")

    _files, desc = _run_turn(repo, mutate)
    out = diff_turn(snap, desc, path="a.txt")
    assert [f["file"] for f in out["files"]] == ["a.txt"]
    assert "+v2" in out["files"][0]["diff"]


def test_diff_turn_external_file_uses_archive_blob(repo, tmp_path):
    """外部文件没有 git tree，靠归档 blob 与当前内容做 difflib 对比。"""
    work, snap = repo
    ext = tmp_path / "outside" / "ext.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("OLD-A\nOLD-B\n", encoding="utf-8")

    def mutate():
        archive_external(ext)
        ext.write_text("OLD-A\nNEW-B\n", encoding="utf-8")

    _files, desc = _run_turn(repo, mutate)
    out = diff_turn(snap, desc)
    entry = [f for f in out["files"] if f["file"] == str(ext)]
    assert entry, "外部文件应出现在 diff 结果里"
    assert "-OLD-B" in entry[0]["diff"]
    assert "+NEW-B" in entry[0]["diff"]


def test_diff_turn_old_descriptor_reports_reason(repo):
    """旧消息没有 after_tree → 明确 reason，不抛错。"""
    work, snap = repo
    _files, desc = _run_turn(repo, lambda: (work / "a.txt").write_text("v2\n", encoding="utf-8"))
    legacy = {k: v for k, v in desc.items() if k != "after_tree"}
    out = diff_turn(snap, legacy)
    assert out["files"] == []
    assert "after_tree" in out["reason"]


def test_diff_turn_empty_and_missing_inputs(repo):
    assert diff_turn(None, {})["reason"]
    assert diff_turn(None, {"internal": ["x"], "before_tree": "a" * 40, "after_tree": "b" * 40})["reason"]


# ── 每 step 快照（对齐 opencode：每次写工具调用后一个增量 tree）────────────


def test_record_write_after_each_write_keeps_one_step(repo):
    """写前 archive_external、写后 record_write → 每个文件各记一步，路径存相对路径。"""
    work, snap = repo
    (work / "a.txt").write_text("v0\n", encoding="utf-8")

    def mutate():
        (work / "a.txt").write_text("v1\n", encoding="utf-8")
        record_write(work / "a.txt")
        (work / "a.txt").write_text("v2\n", encoding="utf-8")
        record_write(work / "a.txt")

    _files, desc = _run_turn(repo, mutate)
    steps = desc["steps"]
    assert [s["seq"] for s in steps] == [0, 1]
    # 相对路径（与 internal 一致；绝对路径会随机器变化、不该落库）
    assert all(s["files"] == ["a.txt"] for s in steps)
    assert steps[0]["hash"] != steps[1]["hash"]


def test_step_diff_is_incremental_between_consecutive_steps(repo):
    """整轮只有净 diff，step diff 才能看出「中间那一版改了什么」。"""
    work, snap = repo
    (work / "a.txt").write_text("v0\n", encoding="utf-8")

    def mutate():
        # 与生产一致：先写、后 record_write（writer/patch 里都是写完才记 step）
        for v in ("v1", "v2", "v3"):
            (work / "a.txt").write_text(v + "\n", encoding="utf-8")
            record_write(work / "a.txt")

    _files, desc = _run_turn(repo, mutate)
    assert len(desc["steps"]) == 3

    s0 = diff_turn(snap, desc, step=0)["files"][0]["diff"]
    s1 = diff_turn(snap, desc, step=1)["files"][0]["diff"]
    s2 = diff_turn(snap, desc, step=2)["files"][0]["diff"]
    assert "-v0" in s0 and "+v1" in s0
    assert "-v1" in s1 and "+v2" in s1
    assert "-v2" in s2 and "+v3" in s2
    # 整轮净 diff 只剩首尾
    full = diff_turn(snap, desc)["files"][0]["diff"]
    assert "-v0" in full and "+v3" in full
    assert "+v1" not in full and "+v2" not in full
    assert diff_turn(snap, desc)["steps"] == 3


def test_step_diff_only_touches_that_step_files(repo):
    work, snap = repo
    (work / "a.txt").write_text("v0\n", encoding="utf-8")
    (work / "b.txt").write_text("w0\n", encoding="utf-8")

    def mutate():
        (work / "a.txt").write_text("v1\n", encoding="utf-8")
        record_write(work / "a.txt")
        (work / "b.txt").write_text("w1\n", encoding="utf-8")
        record_write(work / "b.txt")

    _files, desc = _run_turn(repo, mutate)
    assert [f["file"] for f in diff_turn(snap, desc, step=0)["files"]] == ["a.txt"]
    assert [f["file"] for f in diff_turn(snap, desc, step=1)["files"]] == ["b.txt"]


def test_step_out_of_range_clamps_to_last(repo):
    work, snap = repo

    def mutate():
        (work / "a.txt").write_text("v1\n", encoding="utf-8")
        record_write(work / "a.txt")

    _files, desc = _run_turn(repo, mutate)
    assert diff_turn(snap, desc, step=99)["files"] == diff_turn(snap, desc, step=0)["files"]


def test_step_diff_without_steps_reports_reason(repo):
    """老消息没有 steps → 明确提示，不抛错。"""
    work, snap = repo
    _files, desc = _run_turn(repo, lambda: (work / "a.txt").write_text("v2\n", encoding="utf-8"))
    legacy = {k: v for k, v in desc.items() if k != "steps"}
    out = diff_turn(snap, legacy, step=0)
    assert out["files"] == []
    assert "逐步快照" in out["reason"]


def test_record_write_ignores_external_paths(repo, tmp_path):
    """工作区外的路径不进影子仓库（外部文件由归档 blob 负责），不产生 step。"""
    work, snap = repo
    ext = tmp_path / "outside" / "x.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("a\n", encoding="utf-8")

    def mutate():
        ext.write_text("b\n", encoding="utf-8")
        record_write(ext)
        (work / "in.txt").write_text("c\n", encoding="utf-8")
        record_write(work / "in.txt")

    _files, desc = _run_turn(repo, mutate)
    assert all("outside" not in f for s in desc["steps"] for f in s["files"])


def test_record_write_without_active_turn_is_noop(repo):
    """无活跃 turn → 静默返回（工具在 chat 之外被调用也不能炸）。"""
    work, snap = repo
    record_write(work / "a.txt")  # 不抛异常即通过
    assert diff_turn(snap, {})["reason"]


def test_steps_capped(repo):
    """长任务写很多次也不让 descriptor 无限膨胀（只保留最近 N 步）。"""
    from app.snapshot.turn import _MAX_STEPS

    work, snap = repo

    def mutate():
        for i in range(_MAX_STEPS + 12):
            (work / f"f{i}.txt").write_text(f"v{i}\n", encoding="utf-8")
            record_write(work / f"f{i}.txt")

    _files, desc = _run_turn(repo, mutate)
    assert len(desc["steps"]) == _MAX_STEPS


def test_track_paths_accepts_relative_and_absolute(repo):
    work, snap = repo
    (work / "a.txt").write_text("v0\n", encoding="utf-8")
    h0 = snap.track()
    (work / "a.txt").write_text("v1\n", encoding="utf-8")
    assert snap.track_paths([work / "a.txt"]) != h0
    assert snap.track_paths(["a.txt"]) == snap.track_paths([str(work / "a.txt")])
    assert snap.track_paths([]) is None


# ── 跨 Agent 事件循环的 turn 传递（bus 事件循环 task 在启动时创建，contextvar 传不进去）──


def test_payload_roundtrip_restores_active_turn(repo, tmp_path):
    """请求侧挂进 payload 的 turn，Agent 侧 bind 后 record_write / archive_external 生效。"""
    work, snap = repo
    outside = tmp_path / "outside" / "new.txt"

    turn = start_turn(snap, "turn_payload")
    payload = payload_with_turn({}, active_turn())
    end_turn()  # 模拟「事件循环 task 看不到请求侧 contextvar」
    assert active_turn() is None

    token = bind_from_payload(payload)
    try:
        assert active_turn() is turn
        archive_external(outside)          # 写前归档：文件不存在 → before=absent
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("hi\n", encoding="utf-8")
        record_write(outside)
    finally:
        reset(token)
    assert active_turn() is None

    files, desc = turn.finalize([])
    assert [f["file"] for f in files] == [str(outside)]
    assert files[0]["status"] == "added" and files[0]["external"] is True
    assert desc["external"][0]["before"] == "absent"


def test_bind_from_payload_noop_without_turn():
    assert bind_from_payload(None) is None
    assert bind_from_payload({}) is None
    assert bind_from_payload({"_turn_snapshot": None}) is None
    assert payload_with_turn(None, None) == {}


def test_bus_dispatch_binds_turn_from_payload(repo):
    """bus._dispatch 必须在 handler task 内重绑 turn（回归：per-step 快照恒空）。"""
    import asyncio
    import inspect

    from app.agent.bus import AgentBus

    work, snap = repo
    src = inspect.getsource(AgentBus._dispatch)
    assert "bind_from_payload" in src, "bus._dispatch 未重绑 turn"
    assert "snap_turn.reset" in src, "bus._dispatch 未 reset turn token"

    turn = start_turn(snap, "turn_bus")
    payload = payload_with_turn({}, turn)
    end_turn()

    async def _handler_like():
        token = bind_from_payload(payload)
        try:
            return active_turn() is turn
        finally:
            reset(token)

    assert asyncio.run(_handler_like()) is True
    assert active_turn() is None


def test_external_created_file_appears_in_files_changed(repo, tmp_path):
    """工作区外新建文件必须出现在 files_changed（此前因 contextvar 丢失恒为空）。"""
    work, snap = repo
    outside = tmp_path / "ext" / "f.txt"

    turn = start_turn(snap, "turn_ext_changed")
    turn.capture_before()
    archive_external(outside)
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("a\nb\n", encoding="utf-8")
    record_write(outside)
    files, _desc = turn.finalize([])
    end_turn()

    assert len(files) == 1
    assert files[0]["file"] == str(outside)
    assert files[0]["additions"] == 2 and files[0]["external"] is True