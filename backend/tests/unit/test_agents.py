"""G-05: markdown agent registry discovery and overriding."""

from __future__ import annotations

from pathlib import Path

from agents import registry as mod
from agents.registry import delete_agent, load_registry, parse_agent_file, save_agent
from agents.resolver import AgentResolver


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_builtin_agents_load():
    reg = load_registry()
    names = {agent.name for agent in reg.list()}
    assert {"research", "code", "file"} <= names
    assert reg.get("research").is_subagent
    assert "write_file" not in reg.get("research").tools
    assert "shell_exec" in reg.get("code").tools


def test_project_overrides_builtin(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin"
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    _write(builtin / "review.md", "---\ndescription: builtin\ntools: [read_file]\n---\nPROMPT")
    _write(global_dir / "review.md", "---\ndescription: global\ntools: read_file, grep\n---\n")

    monkeypatch.setattr(
        mod,
        "_agent_dirs",
        lambda workspace: [
            ("builtin", builtin),
            ("global", global_dir),
            ("project", project),
        ],
    )
    found = load_registry(project.parent)
    assert found.get("review").description == "global"
    assert found.get("review").tools == ("read_file", "grep")

    _write(project / "review.md", "---\ndescription: project\nmode: all\n---\n")
    found = load_registry(project.parent)
    assert found.get("review").description == "project"
    assert found.get("review").source == "project"
    assert found.get("review").is_primary


def test_hidden_and_mode_filters(tmp_path, monkeypatch):
    directory = tmp_path / "agents"
    _write(directory / "visible.md", "---\nmode: primary\n---\n")
    _write(directory / "secret.md", "---\nhidden: true\n---\n")
    monkeypatch.setattr(mod, "_agent_dirs", lambda workspace: [("builtin", directory)])

    reg = load_registry()
    assert {a.name for a in reg.list()} == {"visible"}
    assert reg.list(mode="primary")[0].name == "visible"
    assert reg.list(mode="subagent") == []
    assert reg.subagents() == {}


def test_invalid_yaml_is_skipped(tmp_path):
    bad = _write(tmp_path / "bad.md", "---\ntools: [unclosed\n---\nbody")
    assert parse_agent_file(bad, "builtin") is None


def test_prompt_body_wins_over_path(tmp_path):
    path = _write(tmp_path / "body.md", "---\nname: body\n---\nCustom body")
    agent = parse_agent_file(path, "builtin")
    assert agent.system_prompt() == "Custom body"


def test_nested_id_and_metadata(tmp_path, monkeypatch):
    directory = tmp_path / "agents"
    _write(
        directory / "team" / "review.md",
        "---\nsteps: 9\ndisabled: true\ncolor: '#fff'\nmodel: deepseek/x\npermission:\n  edit: deny\n---\n",
    )
    monkeypatch.setattr(mod, "_agent_dirs", lambda workspace: [("builtin", directory)])
    reg = load_registry()
    agent = reg.get("team/review")
    assert agent is not None
    assert agent.max_rounds == 9
    assert agent.disabled and agent.color == "#fff" and agent.model == "deepseek/x"
    assert reg.list() == []
    assert [a.name for a in reg.list(include_disabled=True)] == ["team/review"]


def test_save_and_delete_project_agent(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    saved = save_agent(
        {
            "name": "custom/checker",
            "description": "checks things",
            "mode": "subagent",
            "model": "deepseek/x",
            "tools": ["read_file"],
            "permission": {"edit": "deny"},
            "max_rounds": 4,
            "prompt_body": "You check things.",
        },
        workspace=workspace,
        scope="project",
    )
    assert saved.name == "custom/checker"
    assert (workspace / ".agentsuper" / "agents" / "custom" / "checker.md").is_file()
    reg = load_registry(workspace)
    assert reg.get("custom/checker").system_prompt() == "You check things."
    assert delete_agent("custom/checker", workspace=workspace, scope="project")
    assert not delete_agent("custom/checker", workspace=workspace, scope="project")


def test_resolver_falls_back_to_build(tmp_path, monkeypatch):
    directory = tmp_path / "agents"
    _write(directory / "build.md", "---\nmode: primary\n---\n")
    _write(directory / "plan.md", "---\nmode: primary\n---\n")
    _write(directory / "hidden.md", "---\nmode: primary\nhidden: true\n---\n")
    monkeypatch.setattr(mod, "_agent_dirs", lambda workspace: [("builtin", directory)])
    resolver = AgentResolver(load_registry())
    assert resolver.primary("plan").name == "plan"
    assert resolver.primary("hidden").name == "build"
    assert resolver.primary("missing").name == "build"


def test_role_agent_prompt_override(monkeypatch):
    from agents import roles
    from agents.registry import AgentDef

    class _Reg:
        def get(self, name):
            return AgentDef(name=name, prompt_body="CUSTOM ROLE")

    monkeypatch.setattr(roles, "load_registry", lambda workspace=None: _Reg())
    assert roles.role_prompt("plan", "DEFAULT") == "CUSTOM ROLE"
    assert roles.role_prompt("missing", "DEFAULT") == "DEFAULT"


def test_request_merges_across_sources(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin"
    global_dir = tmp_path / "global"
    project = tmp_path / "project"
    _write(builtin / "a.md", "---\nrequest:\n  a: 1\n  keep: base\ndescription: base\n---\n")
    _write(global_dir / "a.md", "---\nrequest:\n  b: 2\n  keep: global\n---\n")
    _write(project / "a.md", "---\nrequest:\n  keep: project\ndescription: final\n---\n")
    monkeypatch.setattr(
        mod,
        "_agent_dirs",
        lambda workspace: [("builtin", builtin), ("global", global_dir), ("project", project)],
    )
    agent = load_registry(project.parent).get("a")
    assert agent.description == "final"
    assert agent.request == {"a": 1, "b": 2, "keep": "project"}
