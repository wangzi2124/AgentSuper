"""白名单（工作目录）语义：只有白名单目录免审，其余一律 ask。

审批结果只在本回合有效，不产生任何持久免审记录。
"""

import os
import tempfile
from pathlib import Path

import pytest

from execution.permission import PermissionManager


def _manager(tmp_path, external_default="ask"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return PermissionManager(
        workspace=str(workspace),
        whitelist_path=str(data),
        external_default=external_default,
    )


def test_whitelist_dir_persists_and_allows_external_path(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    target = external / "a.txt"
    mgr = _manager(tmp_path)

    assert mgr.check(str(target), "read") == "ask"
    mgr.add_workspace(str(external))
    assert mgr.check(str(target), "read") == "allow"

    reloaded = _manager(tmp_path)
    assert str(external.resolve()) in reloaded.list_extra_workspaces()
    assert reloaded.check(str(target), "read") == "allow"

    assert reloaded.remove_workspace(str(external)) is True
    assert reloaded.list_extra_workspaces() == []
    assert reloaded.check(str(target), "read") == "ask"


def test_add_workspace_requires_existing_dir_unless_create(tmp_path):
    missing = tmp_path / "missing" / "nested"
    mgr = _manager(tmp_path)

    with pytest.raises(FileNotFoundError):
        mgr.add_workspace(str(missing))

    resolved = mgr.add_workspace(str(missing), create=True)
    assert resolved.is_dir()
    assert str(resolved) in mgr.list_extra_workspaces()


def test_add_workspace_rejects_relative_and_files(tmp_path):
    mgr = _manager(tmp_path)
    with pytest.raises(ValueError):
        mgr.add_workspace("relative/dir")

    file_path = tmp_path / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        mgr.add_workspace(str(file_path))


def test_add_workspace_deduped(tmp_path):
    external = tmp_path / "External"
    external.mkdir()
    mgr = _manager(tmp_path)

    mgr.add_workspace(str(external))
    mgr.add_workspace(str(external))
    assert len(mgr.list_extra_workspaces()) == 1
    if os.name == "nt":
        mgr.add_workspace(str(external).upper())
        assert len(mgr.list_extra_workspaces()) == 1


def test_program_source_dir_can_be_whitelisted(tmp_path):
    """仓库根/源码目录允许显式加入白名单；写保护由审批层兜底。"""
    mgr = _manager(tmp_path)
    mgr.add_workspace(str(mgr.workspace))
    assert str(mgr.workspace.resolve()) in mgr.list_extra_workspaces()
    # 源码写入仍需审批（不在本回合 turn 审批内）
    assert mgr.check(str(mgr.workspace / "app" / "main.py"), "write") == "ask"


def test_temp_path_is_not_auto_allowed(tmp_path):
    mgr = _manager(tmp_path)
    temp_target = str(Path(tempfile.gettempdir()).resolve() / "agent_test.txt")
    assert mgr.classify_path(temp_target) == "temp"
    assert mgr.check(temp_target, "read") == "ask"


def test_approval_is_turn_scoped(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    target = external / "b.txt"
    mgr = _manager(tmp_path)

    req = mgr.create_request(str(target), "write", tool_name="write_file")
    assert mgr.respond(req.id, "allowed") is True
    assert mgr.check(str(target), "write") == "ask"


def test_base_command_strips_env_prefix_and_handles_quotes():
    from execution.permission import base_command, command_keys

    assert base_command("FOO=1 BAR=2 git status") == "git"
    assert base_command("cat a.txt | grep x") == "cat"
    assert base_command("a && b || c") == "a"
    assert base_command('"C:\\Program Files\\node.exe" --version') == "c:\\program files\\node.exe"
    assert base_command("   ") == ""
    assert command_keys("FOO=1 git status | grep x") == ["git", "grep"]
    assert command_keys("cat a > out.txt") == []  # 重定向 → 强制人工审批
    assert command_keys("echo $(curl evil)") == []  # 命令替换 → 强制人工审批


def test_command_asks_until_turn_approval():
    tmp = Path(tempfile.mkdtemp(prefix="perm-cmd-"))
    mgr = PermissionManager(workspace=str(tmp), whitelist_path=str(tmp / "data"))
    assert mgr.check_command("git") == "ask"
    # 临时命令授权不再参与免审（external_default 已废止）
    mgr.add_temp_command_approval("git")
    assert mgr.check_command("git") == "ask"
    mgr.add_session_command_approval("s1", "git")
    assert mgr.check_command("git", session_id="s1") == "allow"
    assert mgr.check_command("git", session_id="s2") == "ask"
    assert mgr.check_command("rm", session_id="s1") == "ask"
    mgr.clear_session_command_approvals("s1")
    assert mgr.check_command("git", session_id="s1") == "ask"


def test_external_default_allow_does_not_affect_paths_or_commands(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    mgr = _manager(tmp_path, external_default="allow")
    assert mgr.check(str(external / "x.txt"), "read") == "ask"
    assert mgr.check_command("git") == "ask"


def test_read_and_shell_tools_require_approval(tmp_path):
    from execution.filesystem import VirtualFileSystem
    from graph.main_graph import default_registry

    registry, _ = default_registry(VirtualFileSystem(root=str(tmp_path)), with_shell=True)
    for name in ("read_file", "ls", "grep", "glob", "write_file", "shell_exec"):
        td = registry.get(name)
        assert td is not None, name
        assert td.require_approval is True, name


def test_command_path_tokens_extracts_external_paths():
    from execution.permission import command_path_tokens

    assert command_path_tokens("cat D:\\secret.txt") == ["D:\\secret.txt"]
    assert command_path_tokens("type ..\\outside.txt") == ["..\\outside.txt"]
    assert command_path_tokens("dir /S") == []
    assert command_path_tokens("curl https://example.com/a") == []
    assert command_path_tokens("python -m pytest") == []
    assert command_path_tokens("tool --out=./dist/out.txt") == ["./dist/out.txt"]
    assert command_path_tokens("echo hi > D:\\x.txt") == ["D:\\x.txt"]
    assert command_path_tokens('cat "C:\\Program Files\\a.txt"') == [
        "C:\\Program Files\\a.txt"
    ]


def test_sensitive_path_detection():
    from execution.permission import is_sensitive_path

    assert is_sensitive_path("E:/AgentSuper/backend/.env") is True
    assert is_sensitive_path("E:/AgentSuper/backend/.env.local") is True
    assert is_sensitive_path("E:/AgentSuper/backend/data/session.db") is True
    assert is_sensitive_path("E:/AgentSuper/backend/data/permissions.json") is True
    assert is_sensitive_path("E:/AgentSuper/.git/config") is True
    assert is_sensitive_path("E:/AgentSuper/backend/app/main.py") is False
    assert is_sensitive_path("E:/AgentSuper/README.md") is False


def test_extra_workspaces_exclude_program_workspace(tmp_path):
    mgr = _manager(tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    mgr.add_workspace(str(extra))

    assert str(extra.resolve()) in mgr.list_extra_workspaces()
    assert str(mgr.workspace.resolve()) not in mgr.list_extra_workspaces()
    assert str(mgr.workspace.resolve()) in mgr.list_workspaces()
