"""G-02: markdown command loading, rendering, and slash precedence."""

from __future__ import annotations

from pathlib import Path

from app.commands import registry as mod
from app.commands.registry import load_commands, parse_command_file
from app.skills.slash import apply_slash_skill


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_frontmatter_and_render(tmp_path, monkeypatch):
    directory = tmp_path / "commands"
    _write(
        directory / "review.md",
        "---\n"
        "description: Review a file\n"
        "agent: code\n"
        "files: [extra.md]\n"
        "---\n"
        "Review $1 with $ARGUMENTS\n",
    )
    _write(tmp_path / "extra.md", "EXTRA")
    monkeypatch.setattr(mod, "command_dirs", lambda workspace: [("global", directory)])

    reg = load_commands()
    command = reg.get("review")
    assert command.agent == "code"
    assert command.files == ("extra.md",)
    out = command.render("alpha beta", tmp_path)
    assert "Review alpha with alpha beta" in out
    assert "EXTRA" in out
    assert out.startswith("[命令：review]")
    assert "Agent：code" in out


def test_project_overrides_global(tmp_path, monkeypatch):
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    _write(global_dir / "x.md", "---\ndescription: global\n---\nG")
    _write(project_dir / "x.md", "---\ndescription: project\n---\nP")
    monkeypatch.setattr(
        mod,
        "command_dirs",
        lambda workspace: [("global", global_dir), ("project", project_dir)],
    )
    command = load_commands(tmp_path / "repo").get("x")
    assert command.description == "project"
    assert command.source == "project"


def test_inline_file_reference(tmp_path, monkeypatch):
    directory = tmp_path / "commands"
    _write(directory / "doc.md", "---\n---\nLook at @notes.txt\n")
    _write(tmp_path / "notes.txt", "NOTES")
    monkeypatch.setattr(mod, "command_dirs", lambda workspace: [("global", directory)])

    out = load_commands().get("doc").render("", tmp_path)
    assert 'path="notes.txt"' in out
    assert "NOTES" in out


def test_invalid_frontmatter_is_skipped(tmp_path):
    assert parse_command_file(_write(tmp_path / "bad.md", "---\n[a\n---\n"), "global") is None


def test_slash_command_wins_over_skill(tmp_path, monkeypatch):
    directory = tmp_path / "commands"
    _write(directory / "docx.md", "---\n---\nCOMMAND BODY\n")
    monkeypatch.setattr(mod, "command_dirs", lambda workspace: [("global", directory)])

    class _Skill:
        enabled = True

    class _Loader:
        def get(self, name):
            return _Skill() if name == "docx" else None

        def get_skill_body(self, name):
            return "SKILL BODY"

    out, name = apply_slash_skill("/docx 转 PDF", _Loader(), workspace=tmp_path)
    assert name == "docx"
    assert "COMMAND BODY" in out
    assert "SKILL BODY" not in out


def test_slash_command_sets_agent_model_override(tmp_path, monkeypatch):
    from app.commands.context import consume_command_override

    directory = tmp_path / "commands"
    _write(
        directory / "fast.md",
        "---\nagent: code\nmodel: deepseek/deepseek-v4-flash\n---\nDo $ARGUMENTS",
    )
    monkeypatch.setattr(mod, "command_dirs", lambda workspace: [("global", directory)])
    out, name = apply_slash_skill("/fast x", workspace=tmp_path)
    assert name == "fast"
    override = consume_command_override()
    assert override == {"agent": "code", "model": "deepseek/deepseek-v4-flash"}
    assert consume_command_override() == {}
