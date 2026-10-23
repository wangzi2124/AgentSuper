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
