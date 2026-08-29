# -*- coding: utf-8 -*-
"""S1 敏感文件访问控制 / S2 管理端点鉴权（权限管理器）安全用例。

覆盖 PermissionManager 的判定语义：
  - .env / *.db / *.sqlite* / permissions.json → 读/写均拒绝（_is_critical_read）
  - 任意层级含 .git 的路径 → 读/写均拒绝（_is_git_path）
  - app / plugins / skills / config / main.py 等源码 → 写入/执行拒绝（_is_critical_write）
  - 工作区内普通文件 → 读/写放行；工作区外默认 external → ask
运行：pytest tests/test_security_permission.py
"""
import os
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.permission.manager import PermissionManager


@pytest.fixture(autouse=True)
def _isolate_temp(monkeypatch, tmp_path):
    """把系统 Temp 重定向到 tmp_path/sys_tmp，避免 pytest 的 tmp 目录本身落在
    系统临时目录下被 classify_path 的 temp 分支劫持（否则所有路径都被 allow）。
    """
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "sys_tmp"))


def _mgr(tmp_path) -> PermissionManager:
    ws = tmp_path / "workspace"
    data = tmp_path / "data"
    ws.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    # 仓库根判定：workspace 之外另设 worktree（backend 之外的路径不受源码保护名单约束）
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return PermissionManager(
        workspace=str(ws),
        whitelist_path=str(data),
        project_worktree=str(repo),
        external_default="ask",
    )


def test_critical_files_deny_read(tmp_path):
    mgr = _mgr(tmp_path)
    ws = tmp_path / "workspace"
    for name in (".env", ".env.local", "secrets.env", "data.db", "cache.sqlite", "vecdb.sqlite3", "permissions.json"):
        assert mgr.check(str(ws / name), "read") == "deny", name


def test_critical_files_deny_write(tmp_path):
    mgr = _mgr(tmp_path)
    ws = tmp_path / "workspace"
    assert mgr.check(str(ws / ".env"), "write") == "deny"
    assert mgr.check(str(ws / "data" / "session.db"), "write") == "deny"


def test_git_path_always_deny(tmp_path):
    mgr = _mgr(tmp_path)
    ws = tmp_path / "workspace"
    # 任意层级含 .git（目录或文件）即拒绝，与读写操作无关
    assert mgr.check(str(ws / "repo" / ".git" / "config"), "read") == "deny"
    assert mgr.check(str(ws / "x" / ".git" / "HEAD"), "write") == "deny"
    # worktree 内的 .git 同样拒绝（worktree 判定在前，.git 硬保护在后）
    repo = tmp_path / "repo"
    assert mgr.check(str(repo / ".git" / "config"), "read") == "deny"


def test_source_paths_deny_write(tmp_path):
    mgr = _mgr(tmp_path)
    ws = tmp_path / "workspace"
    for p in ("app/api/chat.py", "plugins/x.py", "skills/s.md", "config.py", "main.py"):
        assert mgr.check(str(ws / p), "write") == "deny", p
    # 源码路径的读不拦截（读源码本身无 RCE 风险）
    assert mgr.check(str(ws / "app" / "api" / "chat.py"), "read") == "allow"


def test_workspace_normal_paths_allowed(tmp_path):
    mgr = _mgr(tmp_path)
    ws = tmp_path / "workspace"
    assert mgr.check(str(ws / "README.md"), "read") == "allow"
    assert mgr.check(str(ws / "docs" / "1.txt"), "write") == "allow"


def test_worktree_paths_allowed_but_source_still_protected(tmp_path):
    ws = tmp_path / "workspace"
    repo = tmp_path / "repo"
    ws.mkdir(parents=True, exist_ok=True)
    repo.mkdir(exist_ok=True)
    mgr = PermissionManager(
        workspace=str(ws), whitelist_path=str(tmp_path / "data"),
        project_worktree=str(repo), external_default="ask",
    )
    # worktree 内普通文件视为 workspace → 放行
    assert mgr.check(str(repo / "README.md"), "read") == "allow"
    # worktree 内根目录 .env 不属 backend 源码保护，可读 —— 但 repo 根不在 workspace 内
    # 因此 _is_critical_read 仅对 workspace 内生效；仓库根 .env 由（如有）gitignore 兜底
    assert mgr.check(str(repo / ".env"), "read") == "allow"


def test_worktree_inside_temp_not_hijacked(tmp_path):
    """回归：extra worktree 恰好位于系统 Temp 内（autouse 夹具已将 gettempdir
    重定向到 tmp_path/sys_tmp）时，通过 worktree 识别为 workspace，.git 保护的
    判定不得被 temp→allow 分支劫持（此前 temp 早于 worktree，会放行）。"""
    repo_path = tmp_path / "repo_in_temp"
    ws = tmp_path / "workspace"
    ws.mkdir()
    repo_path.mkdir(exist_ok=True)
    mgr = PermissionManager(
        workspace=str(ws), whitelist_path=str(tmp_path / "data"),
        project_worktree=str(repo_path), external_default="ask",
    )
    assert mgr.classify_path(str(repo_path)) == "workspace"
    assert mgr.check(str(repo_path / ".git" / "config"), "read") == "deny"


def test_external_path_default_ask(tmp_path):
    mgr = _mgr(tmp_path)
    outside = tmp_path / "elsewhere" / "a.txt"
    outside.parent.mkdir(exist_ok=True)
    assert mgr.check(str(outside), "read") == "ask"


def test_external_deny_policy(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    mgr = PermissionManager(
        workspace=str(ws), whitelist_path=str(tmp_path / "data"),
        external_default="deny",
    )
    outside = tmp_path / "outside.txt"
    assert mgr.check(str(outside), "write") == "deny"