from pathlib import Path

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


def test_whitelist_persists_and_allows_external_path(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    target = external / "a.txt"
    mgr = _manager(tmp_path)

    assert mgr.check(str(target), "read") == "ask"
    mgr.add_whitelist(str(external))
    assert mgr.check(str(target), "read") == "allow"

    reloaded = _manager(tmp_path)
    assert str(external.resolve()) in reloaded.list_whitelist()
    assert reloaded.check(str(target), "read") == "allow"

    assert reloaded.remove_whitelist(str(external)) is True
    assert reloaded.list_whitelist() == []
    assert reloaded.check(str(target), "read") == "ask"


def test_allow_and_remember_persists_path_whitelist(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    target = external / "b.txt"
    mgr = _manager(tmp_path)

    req = mgr.create_request(str(target), "write", tool_name="write_file")
    assert mgr.respond(req.id, "allowed", remember=True) is True

    assert str(target.resolve()) in mgr.list_whitelist()
    assert mgr.check(str(target), "write") == "allow"


def test_temp_path_follows_external_default(tmp_path):
    import tempfile

    mgr = _manager(tmp_path)
    temp_target = str(Path(tempfile.gettempdir()).resolve() / "agent_test.txt")
    assert mgr.classify_path(temp_target) == "temp"
    assert mgr.check(temp_target, "read") == "ask"


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


def test_command_whitelist_and_temp_approval_allow():
    import tempfile

    from execution.permission import PermissionManager

    tmp = Path(tempfile.mkdtemp(prefix="perm-cmd-"))
    mgr = PermissionManager(workspace=str(tmp), whitelist_path=str(tmp / "data"))
    assert mgr.check_command("git") == "ask"
    mgr.add_temp_command_approval("git")
    assert mgr.check_command("git") == "allow"
    assert mgr.check_command("rm") == "ask"


def test_read_and_shell_tools_require_approval(tmp_path):
    from execution.filesystem import VirtualFileSystem
    from graph.main_graph import default_registry

    registry, _ = default_registry(VirtualFileSystem(root=str(tmp_path)), with_shell=True)
    for name in ("read_file", "ls", "grep", "glob", "write_file", "shell_exec"):
        td = registry.get(name)
        assert td is not None, name
        assert td.require_approval is True, name
