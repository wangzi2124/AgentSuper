"""Session workspace policy: temp default, immutable root, strict approvals."""

from __future__ import annotations

import tempfile
from pathlib import Path

from execution.filesystem import (
    VirtualFileSystem,
    reset_active_root,
    reset_allowed_roots,
    set_active_root,
    set_allowed_roots,
)


def _default_temp(session_id: str) -> Path:
    return (Path(tempfile.gettempdir()) / "agentsuper" / session_id).resolve()


def test_session_defaults_to_isolated_temp_workspace(client):
    ctx = client.app.state.platform
    r = client.post("/api/sessions", json={"title": "temp-default"})
    assert r.status_code == 200
    info = r.json()
    sid = info["id"]
    try:
        directory = Path(info["directory"]).resolve()
        assert directory == _default_temp(sid)
        assert directory.is_dir()
        assert ctx.sessions.get(sid).directory == str(directory)
        client.delete(f"/api/sessions/{sid}")
        assert not directory.exists()
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_patch_session_cannot_change_directory(client, tmp_path):
    """会话工作目录不可修改（PATCH 只改标题等元数据）。"""
    ctx = client.app.state.platform
    custom = tmp_path / "custom-workspace"
    custom.mkdir()
    r = client.post("/api/sessions", json={"title": "immutable-root"})
    sid = r.json()["id"]
    try:
        original = Path(r.json()["directory"]).resolve()
        # 旧字段被忽略：目录保持不变
        patched = client.patch(f"/api/sessions/{sid}", json={"directory": str(custom)})
        assert patched.status_code == 200
        assert Path(patched.json()["directory"]).resolve() == original
        assert original.exists()
        assert ctx.sessions.get(sid).directory == str(original)

        titled = client.patch(f"/api/sessions/{sid}", json={"title": "renamed"})
        assert titled.status_code == 200
        assert titled.json()["title"] == "renamed"
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_active_root_controls_relative_paths(tmp_path):
    default = tmp_path / "default"
    session = tmp_path / "session"
    session.mkdir()
    vfs = VirtualFileSystem(root=str(default))
    root_token = set_active_root(str(session))
    roots_token = set_allowed_roots([])
    try:
        vfs.write_file("relative.txt", "hello")
        assert (session / "relative.txt").read_text(encoding="utf-8") == "hello"
        assert not (default / "relative.txt").exists()
        assert vfs.is_allowed(str(session / "relative.txt")) is True
        assert vfs.is_allowed(str(default / "relative.txt")) is False
    finally:
        reset_active_root(root_token)
        reset_allowed_roots(roots_token)


def test_allowed_roots_are_session_plus_internal_plus_whitelist(client, tmp_path):
    from execution.permission import PermissionManager, get_manager, set_manager

    ctx = client.app.state.platform
    old_manager = get_manager()
    whitelisted = tmp_path / "whitelisted"
    whitelisted.mkdir()
    mgr = PermissionManager(
        workspace=str(ctx.vfs.root),
        whitelist_path=str(tmp_path / "data"),
        external_default="allow",
    )
    mgr.add_workspace(str(whitelisted))
    set_manager(mgr)
    r = client.post("/api/sessions", json={"title": "roots"})
    sid = r.json()["id"]
    try:
        roots = ctx.allowed_roots_for(sid)
        assert ctx.sessions.get(sid).directory in roots
        assert str(whitelisted.resolve()) in roots
        # 程序内部数据目录隐式免审
        assert any(p.endswith(str(Path("data") / "truncation")) for p in roots)
        # 程序源码/仓库根不在免审范围
        assert str(ctx.vfs.root.resolve()) not in roots
    finally:
        client.delete(f"/api/sessions/{sid}")
        set_manager(old_manager)


def test_program_source_is_not_auto_allowed(client, tmp_path_factory):
    """仓库根/源码目录不再隐式免审；加入白名单后写入源码仍需审批。"""
    from execution.permission import PermissionManager

    ctx = client.app.state.platform
    repo = tmp_path_factory.mktemp("repo")
    backend = repo / "backend"
    (backend / "app").mkdir(parents=True)
    source = backend / "app" / "main.py"
    source.write_text("print('x')", encoding="utf-8")
    readme = repo / "README.md"
    readme.write_text("hi", encoding="utf-8")

    mgr = PermissionManager(
        workspace=str(backend),
        whitelist_path=str(repo.parent / "data-source-policy"),
        external_default="allow",
        project_worktree=str(repo),
    )

    token = set_allowed_roots([])
    try:
        assert ctx._path_decision(str(readme), "read", mgr) == "ask"
        assert ctx._path_decision(str(source), "read", mgr) == "ask"
    finally:
        reset_allowed_roots(token)

    token = set_allowed_roots([str(repo)])
    try:
        # 白名单放行普通文件读取
        assert ctx._path_decision(str(readme), "read", mgr) == "allow"
        # 源码保护优先：白名单里的源码写入仍需审批
        assert ctx._path_decision(str(source), "write", mgr) == "ask"
    finally:
        reset_allowed_roots(token)


def test_workspace_api_lists_only_whitelist(client, tmp_path):
    """`/api/permission/workspaces` 只暴露用户白名单（不含程序主工作区）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import permission as perm_api
    from execution.permission import PermissionManager, get_manager, set_manager

    old_manager = get_manager()
    ctx = client.app.state.platform
    whitelisted = tmp_path / "listed"
    whitelisted.mkdir()
    mgr = PermissionManager(
        workspace=str(ctx.vfs.root),
        whitelist_path=str(tmp_path / "data"),
        external_default="ask",
    )
    set_manager(mgr)

    api = FastAPI()
    api.include_router(perm_api.router, prefix="/api")
    try:
        with TestClient(api, client=("127.0.0.1", 50000)) as c:
            created = c.post("/api/permission/workspaces", json={"path": str(whitelisted)})
            assert created.status_code == 200
            assert created.json()["workspaces"] == [str(whitelisted.resolve())]

            missing = c.post(
                "/api/permission/workspaces",
                json={"path": str(tmp_path / "missing-dir")},
            )
            assert missing.status_code == 404
            assert missing.json()["detail"] == "directory_not_found"

            listing = c.get("/api/permission/workspaces").json()
            assert listing["workspaces"] == [str(whitelisted.resolve())]
            assert str(ctx.vfs.root.resolve()) not in listing["workspaces"]

            removed = c.delete(
                "/api/permission/workspaces",
                params={"path": str(whitelisted)},
            )
            assert removed.status_code == 200
            assert removed.json()["workspaces"] == []
    finally:
        set_manager(old_manager)
