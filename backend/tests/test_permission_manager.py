# -*- coding: utf-8 -*-
"""PermissionManager 全量语义用例（补 test_security_permission.py 之外的分支）。

覆盖：
  - 单例 get_manager/set_manager
  - 会话级工作目录 contextvar（set/reset/current、空值与路径规范化）
  - NeedsPermission 异常构造
  - classify_path 全部分类：workspace（主工作区/会话目录/extra/worktree）、
    system、temp、external；invalid external_default 归一化
  - _is_git_path / _is_critical_read / _is_critical_write（含 allow_source_writes）
  - check 决策：workspace 保护、temp→allow、system→deny、临时授权命中
    （自身/父目录/过期清理）、白名单前缀命中、external 默认策略
  - 命令白名单：持久化加载、check_command 命中/过期、add_temp_command_approval LRU
  - 运行时工作区持久化：add/remove/list + runtime_workspaces.json 落盘与重载
  - 审批流：create_request 去重复用、await_decision 超时/未知、respond（含
    remember→路径/命令白名单落盘）、get_pending/get_request/cleanup_expired
运行：pytest tests/test_permission_manager.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.permission.manager as pm
from app.permission.manager import (
    NeedsPermission,
    PermissionManager,
    current_session_workspace,
    get_manager,
    reset_session_workspace,
    set_manager,
    set_session_workspace,
)


@pytest.fixture(autouse=True)
def _isolate_temp(monkeypatch, tmp_path):
    """把系统 Temp 重定向到 tmp_path/sys_tmp，避免 pytest tmp 目录本身落在
    系统临时目录下被 classify_path 的 temp 分支劫持。"""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "sys_tmp"))


def _mgr(tmp_path, **kw) -> PermissionManager:
    ws = tmp_path / "workspace"
    data = tmp_path / "data"
    ws.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    defaults = dict(
        workspace=str(ws),
        whitelist_path=str(data),
        project_worktree="",
        external_default="ask",
    )
    defaults.update(kw)
    return PermissionManager(**defaults)


# ── 单例与 contextvar ──────────────────────────────────────────────────────

def test_get_set_manager(tmp_path):
    before = get_manager()
    assert isinstance(before, PermissionManager)
    m = _mgr(tmp_path)
    set_manager(m)
    try:
        assert get_manager() is m
    finally:
        set_manager(before)


def test_session_workspace_contextvar(tmp_path):
    d = tmp_path / "session_dir"
    token = set_session_workspace(str(d))
    assert current_session_workspace() == str(d.resolve())
    reset_session_workspace(token)
    assert current_session_workspace() == ""


def test_session_workspace_blank_normalized(tmp_path):
    assert set_session_workspace("   ") is not None
    assert current_session_workspace() == ""
    token = set_session_workspace(tmp_path / "rel")
    assert current_session_workspace() == str((tmp_path / "rel").resolve())
    reset_session_workspace(token)
    assert current_session_workspace() == ""


def test_needs_permission_exception():
    e = NeedsPermission("/x/y.txt", "write", "tool_write_file", {"path": "/x/y.txt"})
    assert e.path == "/x/y.txt"
    assert e.operation == "write"
    assert e.tool_name == "tool_write_file"
    assert e.tool_args == {"path": "/x/y.txt"}
    assert str(e) == "Needs permission: write /x/y.txt"
    e2 = NeedsPermission("/z", "read")
    assert e2.tool_name == ""
    assert e2.tool_args == {}


# ── classify_path ──────────────────────────────────────────────────────────

def test_classify_workspace(tmp_path):
    mgr = _mgr(tmp_path)
    assert mgr.classify_path(str(tmp_path / "workspace" / "a" / "b.txt")) == "workspace"


def test_classify_session_workspace(tmp_path):
    mgr = _mgr(tmp_path)
    ses = tmp_path / "sesdir"
    token = set_session_workspace(str(ses))
    try:
        assert mgr.classify_path(str(ses / "f.txt")) == "workspace"
    finally:
        reset_session_workspace(token)
    assert current_session_workspace() == ""


def test_classify_extra_workspace(tmp_path):
    extra = tmp_path / "extra"
    extra.mkdir()
    mgr = _mgr(tmp_path, extra_workspaces=[str(extra)])
    assert mgr.classify_path(str(extra / "x.txt")) == "workspace"


def test_classify_project_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = _mgr(tmp_path, project_worktree=str(repo))
    assert mgr.classify_path(str(repo / "README.md")) == "workspace"


def test_classify_system(tmp_path):
    mgr = _mgr(tmp_path)
    if os.name == "nt":
        assert mgr.classify_path(r"C:\Windows\System32\drivers\etc\hosts") == "system"


def test_classify_temp(tmp_path):
    mgr = _mgr(tmp_path)
    p = tmp_path / "sys_tmp" / "scratch.txt"
    assert mgr.classify_path(str(p)) == "temp"


def test_classify_external(tmp_path):
    mgr = _mgr(tmp_path)
    outside = tmp_path / "elsewhere" / "a.txt"
    assert mgr.classify_path(str(outside)) == "external"


def test_external_default_invalid_coerced(tmp_path):
    mgr = _mgr(tmp_path, external_default="banana")
    assert mgr.external_default == "ask"


def test_approval_timeout_coerced(tmp_path):
    mgr = _mgr(tmp_path, approval_timeout=0)
    assert mgr.approval_timeout == 1


# ── 保护判定 ───────────────────────────────────────────────────────────────

def test_is_git_path(tmp_path):
    mgr = _mgr(tmp_path)
    p = tmp_path / "workspace" / "repo" / ".git" / "config"
    assert mgr._is_git_path(p.resolve()) is True


def test_is_critical_read_only_workspace(tmp_path):
    mgr = _mgr(tmp_path, project_worktree="")
    outside = (tmp_path / "elsewhere").resolve()
    assert mgr._is_critical_read(outside / ".env") is False


def test_is_critical_read_variants(tmp_path):
    mgr = _mgr(tmp_path)
    ws = (tmp_path / "workspace").resolve()
    assert mgr._is_critical_read(ws / "sub" / ".env.local") is True
    assert mgr._is_critical_read(ws / "secrets.env") is True
    assert mgr._is_critical_read(ws / "data" / "app.sqlite3") is True
    assert mgr._is_critical_read(ws / "x" / "permissions.json") is True
    assert mgr._is_critical_read(ws / "README.md") is False


def test_is_critical_write_variants(tmp_path):
    mgr = _mgr(tmp_path)
    ws = (tmp_path / "workspace").resolve()
    assert mgr._is_critical_write(ws / "app" / "api" / "chat.py") is True
    assert mgr._is_critical_write(ws / "plugins" / "x.py") is True
    assert mgr._is_critical_write(ws / "skills" / "s.md") is True
    assert mgr._is_critical_write(ws / "config" / "c.yaml") is True
    assert mgr._is_critical_write(ws / "main.py") is True
    assert mgr._is_critical_write(ws / "requirements.txt") is True
    assert mgr._is_critical_write(ws / "pyproject.toml") is True
    assert mgr._is_critical_write(ws / "docs" / "app.py") is False
    assert mgr._is_critical_write(ws / "README.md") is False
    assert mgr._is_critical_write((tmp_path / "outside" / ".env").resolve()) is False
    # allow_source_writes=True：仅 .git 仍拒绝
    mgr2 = _mgr(tmp_path, allow_source_writes=True)
    assert mgr2._is_critical_write(ws / "plugins" / "x.py") is False
    assert mgr2._is_critical_write(ws / ".git" / "config") is True


# ── check 决策 ─────────────────────────────────────────────────────────────

def test_check_system_deny(tmp_path):
    mgr = _mgr(tmp_path)
    if os.name == "nt":
        assert mgr.check(r"C:\Windows\notepad.exe", "read") == "deny"


def test_check_temp_allow(tmp_path):
    mgr = _mgr(tmp_path)
    assert mgr.check(str(tmp_path / "sys_tmp" / "x.txt"), "write") == "allow"


def test_check_workspace_protection(tmp_path):
    mgr = _mgr(tmp_path)
    ws = tmp_path / "workspace"
    assert mgr.check(str(ws / "app.py"), "read") == "allow"
    assert mgr.check(str(ws / "app" / "x.py"), "execute") == "deny"
    assert mgr.check(str(ws / ".env"), "read") == "deny"
    assert mgr.check(str(ws / ".git" / "HEAD"), "read") == "deny"


def test_check_classify_external_default_allow(tmp_path):
    mgr = _mgr(tmp_path, external_default="allow")
    outside = tmp_path / "elsewhere" / "a.txt"
    assert mgr.check(str(outside), "read") == "allow"


def test_check_temp_approval_self_and_parent(tmp_path):
    mgr = _mgr(tmp_path, external_default="ask")
    outside = tmp_path / "elsewhere" / "dir" / "f.txt"
    outside.parent.mkdir(parents=True)
    assert mgr.check(str(outside), "read") == "ask"
    mgr.add_temp_approval(str(outside))
    assert mgr.check(str(outside), "read") == "allow"
    # 父目录命中
    mgr2 = _mgr(tmp_path, external_default="ask")
    mgr2.add_temp_approval(str(outside.parent))
    assert mgr2.check(str(outside), "read") == "allow"


def test_check_temp_approval_expired_cleaned(tmp_path):
    now = pm.time.time()
    mgr = _mgr(tmp_path, external_default="ask")
    outside = tmp_path / "elsewhere" / "f.txt"
    mgr._temp_approvals[str(outside.resolve())] = now - pm._TEMP_APPROVAL_TTL - 10
    assert mgr.check(str(outside), "read") == "ask"
    assert str(outside.resolve()) not in mgr._temp_approvals


def test_check_whitelist_prefix(tmp_path):
    mgr = _mgr(tmp_path, external_default="ask")
    allowed = tmp_path / "allowed_root"
    mgr._whitelist = [str(allowed)]
    assert mgr.check(str(allowed / "sub" / "f.txt"), "read") == "allow"


def test_add_temp_approval_lru_eviction(monkeypatch, tmp_path):
    monkeypatch.setattr(pm, "_MAX_TEMP_APPROVALS", 5)
    mgr = _mgr(tmp_path)
    for i in range(10):
        mgr.add_temp_approval(str(tmp_path / f"p{i}"))
    assert len(mgr._temp_approvals) <= 5


# ── 命令白名单 ─────────────────────────────────────────────────────────────

def test_command_whitelist_persist_and_reload(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    mgr = _mgr(tmp_path)
    mgr._command_whitelist.add("git diff")
    mgr._save_command_whitelist()
    assert (data / "command_permissions.json").exists()
    mgr2 = PermissionManager(workspace=str(tmp_path / "workspace"), whitelist_path=str(data))
    assert "git diff" in mgr2._command_whitelist
    assert mgr2.check_command("GIT DIFF") == "allow"


def test_command_whitelist_case_insensitive(tmp_path):
    mgr = _mgr(tmp_path)
    mgr._command_whitelist.add("python x.py")
    assert mgr.check_command("PYTHON X.PY") == "allow"


def test_check_command_default(tmp_path):
    mgr = _mgr(tmp_path, external_default="ask")
    assert mgr.check_command("unknown-cmd") == "ask"
    mgr2 = _mgr(tmp_path, external_default="allow")
    assert mgr2.check_command("unknown-cmd") == "allow"


def test_temp_command_approval_and_expiry(tmp_path):
    now = pm.time.time()
    mgr = _mgr(tmp_path, external_default="ask")
    mgr.add_temp_command_approval("node --version")
    assert mgr.check_command("NODE --VERSION") == "allow"
    # 过期清理
    mgr._temp_command_approvals["node --version"] = now - pm._TEMP_APPROVAL_TTL - 5
    assert mgr.check_command("node --version") == "ask"
    assert "node --version" not in mgr._temp_command_approvals
    # LRU 淘汰
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pm, "_MAX_TEMP_APPROVALS", 3)
    mgr2 = _mgr(tmp_path)
    for i in range(6):
        mgr2.add_temp_command_approval(f"cmd{i}")
    assert len(mgr2._temp_command_approvals) <= 3
    monkeypatch.undo()


# ── 运行时工作区 ───────────────────────────────────────────────────────────

def test_add_workspace_creates_and_dedups(tmp_path):
    mgr = _mgr(tmp_path)
    w = tmp_path / "extra_new"
    p = mgr.add_workspace(str(w))
    assert w.exists()
    assert p == w.resolve()
    before = len(mgr.extra_workspaces)
    again = mgr.add_workspace(str(w))
    assert len(mgr.extra_workspaces) == before
    assert again == w.resolve()


def test_runtime_workspaces_persist_and_reload(tmp_path):
    mgr = _mgr(tmp_path)
    w = tmp_path / "rt_ws"
    mgr.add_workspace(str(w))
    assert (tmp_path / "data" / "runtime_workspaces.json").exists()

    mgr2 = _mgr(tmp_path)
    assert any(x == w.resolve() for x in mgr2.extra_workspaces)


def test_add_and_remove_workspace_list(tmp_path):
    mgr = _mgr(tmp_path)
    w = tmp_path / "rm_ws"
    w.mkdir()
    mgr.extra_workspaces.append(w)
    assert str(w) in mgr.list_workspaces()
    assert mgr.remove_workspace(str(w)) is True
    assert str(w) not in mgr.list_workspaces()
    assert mgr.remove_workspace(str(w)) is False


def test_constructor_extra_workspaces_dedup_on_reload(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    first = tmp_path / "first"
    first.mkdir()
    rw = tmp_path / "rt_ws"
    mgr = _mgr(tmp_path, extra_workspaces=[str(first)])
    mgr.add_workspace(str(rw))
    assert len([x for x in mgr.extra_workspaces]) == 2
    mgr2 = PermissionManager(
        workspace=str(tmp_path / "workspace"),
        whitelist_path=str(data),
        extra_workspaces=[str(first), str(rw)],  # 与已持久化重叠 → 去重
    )
    assert len(mgr2.extra_workspaces) == 2


# ── 审批流 ────────────────────────────────────────────────────────────────

def test_create_request_and_dedup_pending(tmp_path):
    mgr = _mgr(tmp_path)
    r1 = mgr.create_request("/x/y.txt", "write", "tool_write_file")
    r2 = mgr.create_request("/x/y.txt", "write", "tool_write_file")
    assert r1 is r2
    assert r1.status == "pending"
    # 终态后新建不复用
    mgr.respond(r1.id, "denied")
    r3 = mgr.create_request("/x/y.txt", "write", "tool_write_file")
    assert r3 is not r1
    assert r3.status == "pending"


async def test_await_decision_resolves(tmp_path):
    mgr = _mgr(tmp_path)
    req = mgr.create_request("/x", "read")
    waiter = asyncio.create_task(mgr.await_decision(req.id))
    await asyncio.sleep(0)
    mgr.respond(req.id, "allowed")
    assert await waiter == "allowed"


async def test_await_decision_timeout(tmp_path):
    mgr = _mgr(tmp_path, approval_timeout=1)
    req = mgr.create_request("/x", "read")
    assert await mgr.await_decision(req.id, timeout=0.01) == "expired"
    assert req.status == "expired"
    assert req.id not in mgr.get_pending_requests()


async def test_await_decision_unknown_request(tmp_path):
    mgr = _mgr(tmp_path)
    assert await mgr.await_decision("nope") == "expired"


def test_respond_remember_path_persists(tmp_path):
    mgr = _mgr(tmp_path, external_default="ask")
    outside = tmp_path / "elsewhere" / "f.txt"
    req = mgr.create_request(str(outside), "read", "tool_read_file")
    assert mgr.respond(req.id, "allowed", remember=True) is True
    assert req.status == "allowed"
    # 白名单落盘 + 生效
    assert str(outside.resolve()) in mgr._whitelist
    assert mgr.check(str(outside), "read") == "allow"
    assert (tmp_path / "data" / "permissions.json").exists()


def test_respond_remember_command(tmp_path):
    mgr = _mgr(tmp_path)
    req = mgr.create_request("git status", "command", "tool_execute")
    assert mgr.respond(req.id, "allowed", remember=True) is True
    assert "git status" in mgr._command_whitelist
    assert mgr.check_command("GIT STATUS") == "allow"
    assert (tmp_path / "data" / "command_permissions.json").exists()


def test_respond_command_does_not_touch_path_whitelist(tmp_path):
    mgr = _mgr(tmp_path)
    req = mgr.create_request("git diff", "command")
    mgr.respond(req.id, "allowed", remember=True)
    assert mgr._whitelist == []


def test_respond_remember_denied_does_not_add(tmp_path):
    mgr = _mgr(tmp_path)
    req = mgr.create_request("/x", "read")
    mgr.respond(req.id, "denied", remember=True)
    assert mgr._whitelist == []


def test_respond_rejects_non_pending(tmp_path):
    mgr = _mgr(tmp_path)
    req = mgr.create_request("/x", "read")
    assert mgr.respond(req.id, "allowed") is True
    assert mgr.respond(req.id, "allowed") is False
    assert mgr.respond("missing", "allowed") is False


def test_pending_requests_and_cleanup(tmp_path):
    mgr = _mgr(tmp_path)
    r1 = mgr.create_request("/a", "read")
    r2 = mgr.create_request("/b", "write")
    assert {r.id for r in mgr.get_pending_requests()} == {r1.id, r2.id}
    assert mgr.get_request(r1.id) is r1
    mgr.respond(r1.id, "allowed")
    mgr.respond(r2.id, "denied")
    mgr.cleanup_expired()
    assert mgr.get_request(r1.id) is None
    assert mgr.get_pending_requests() == []


def test_respond_prunes_pending_index(tmp_path):
    mgr = _mgr(tmp_path)
    r1 = mgr.create_request("/a", "read")
    mgr.respond(r1.id, "allowed")
    r2 = mgr.create_request("/a", "read")  # 复用索引已被修剪 → 新请求
    assert r2 is not r1


# ── 错误路径：损坏的持久化文件不崩溃 ───────────────────────────────────────

def test_load_valid_whitelist(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "permissions.json").write_text(
        json.dumps({"allowed_paths": ["/a", "/b"]}), encoding="utf-8"
    )
    mgr = PermissionManager(
        workspace=str(tmp_path / "workspace"), whitelist_path=str(data)
    )
    assert mgr._whitelist == ["/a", "/b"]


def test_whitelist_no_match_falls_through(tmp_path):
    mgr = _mgr(tmp_path, external_default="ask")
    allowed = tmp_path / "other_root"
    mgr._whitelist = [str(allowed)]
    outside = tmp_path / "elsewhere" / "f.txt"
    assert mgr.check(str(outside), "read") == "ask"


def test_add_workspace_existing_file(tmp_path):
    mgr = _mgr(tmp_path)
    f = tmp_path / "asfile"
    f.write_text("x", encoding="utf-8")
    p = mgr.add_workspace(str(f))
    assert p == f.resolve()
    assert f.is_file()


def test_critical_write_allow_source_outside_workspace(tmp_path):
    mgr = _mgr(tmp_path, allow_source_writes=True)
    outside = (tmp_path / "elsewhere").resolve()
    assert mgr._is_critical_write(outside / "app.py") is False


def test_bad_whitelist_json_no_crash(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "permissions.json").write_text("{not json", encoding="utf-8")
    (data / "command_permissions.json").write_text("###", encoding="utf-8")
    (data / "runtime_workspaces.json").write_text("???", encoding="utf-8")
    mgr = PermissionManager(
        workspace=str(tmp_path / "workspace"), whitelist_path=str(data)
    )
    assert mgr._whitelist == []
    assert mgr._command_whitelist == set()
    assert mgr.extra_workspaces == []


def test_default_constructor(tmp_path):
    mgr = PermissionManager()
    assert mgr.workspace == Path.cwd().resolve()
    assert mgr.extra_workspaces == []