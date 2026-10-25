"""Session workspace policy: temp default, replace-on-set, external approvals."""

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


def test_patch_session_directory_replaces_root_and_reset_returns_temp(client, tmp_path):
    ctx = client.app.state.platform
    custom = tmp_path / "custom-workspace"
    custom.mkdir()
    r = client.post("/api/sessions", json={"title": "custom-root"})
    sid = r.json()["id"]
    try:
        original = Path(r.json()["directory"]).resolve()
        patched = client.patch(f"/api/sessions/{sid}", json={"directory": str(custom)})
        assert patched.status_code == 200
        assert Path(patched.json()["directory"]).resolve() == custom.resolve()
        assert not original.exists()

        invalid = client.patch(
            f"/api/sessions/{sid}", json={"directory": str(tmp_path / "missing")}
        )
        assert invalid.status_code == 400

        ctx.begin_turn(sid)
        running = client.patch(
            f"/api/sessions/{sid}", json={"directory": str(custom)}
        )
        assert running.status_code == 409
        ctx.end_turn(sid)

        reset = client.patch(f"/api/sessions/{sid}", json={"directory": ""})
        assert reset.status_code == 200
        assert Path(reset.json()["directory"]).resolve() == _default_temp(sid)
        assert custom.exists()
    finally:
        ctx.end_turn(sid)
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


def test_allowed_roots_only_use_current_session_not_legacy_whitelist(client, tmp_path):
    from execution.permission import PermissionManager, get_manager, set_manager

    ctx = client.app.state.platform
    old_manager = get_manager()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    mgr = PermissionManager(
        workspace=str(ctx.vfs.root),
        whitelist_path=str(tmp_path / "data"),
        external_default="ask",
    )
    mgr.add_whitelist(str(legacy))
    set_manager(mgr)
    r = client.post("/api/sessions", json={"title": "roots"})
    sid = r.json()["id"]
    try:
        roots = ctx.allowed_roots_for(sid)
        assert roots == [ctx.sessions.get(sid).directory]
        assert str(legacy.resolve()) not in roots
    finally:
        client.delete(f"/api/sessions/{sid}")
        set_manager(old_manager)
