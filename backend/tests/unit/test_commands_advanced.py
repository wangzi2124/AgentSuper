"""Command semantics: template frontmatter, subtask alias, safe shell hook."""

from __future__ import annotations

from pathlib import Path

from app.commands.registry import parse_command_file


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_template_frontmatter_overrides_body(tmp_path):
    path = _write(
        tmp_path / "cmd.md",
        "---\ntemplate: 'Template $1'\n---\nBody text\n",
    )
    command = parse_command_file(path, "global")
    assert command is not None
    assert command.template == "Template $1"
    out = command.render("alpha beta")
    assert "Template alpha" in out
    assert "Body text" not in out


def test_subagent_alias_sets_subtask(tmp_path):
    path = _write(tmp_path / "cmd.md", "---\nsubagent: true\n---\nDo it\n")
    command = parse_command_file(path, "global")
    assert command is not None
    assert command.subtask is True
    assert "（子任务）" in command.render("")


def test_shell_is_not_executed_without_runner(tmp_path):
    path = _write(tmp_path / "cmd.md", "---\n---\nRun !`echo hi`\n")
    command = parse_command_file(path, "global")
    out = command.render("")
    assert "!`echo hi`" in out


def test_shell_uses_approved_runner(tmp_path):
    path = _write(tmp_path / "cmd.md", "---\n---\nRun !`echo hi`\n")
    command = parse_command_file(path, "global")
    calls = []

    def runner(cmd, workspace):
        calls.append(cmd)
        return "hi"

    out = command.render("", shell_runner=runner)
    assert calls == ["echo hi"]
    assert "<shell command='echo hi'>" in out
    assert "hi" in out
    assert "!`echo hi`" not in out


def test_shell_runner_error_is_inline(tmp_path):
    path = _write(tmp_path / "cmd.md", "---\n---\n!`boom`\n")
    command = parse_command_file(path, "global")

    def runner(cmd, workspace):
        raise RuntimeError("denied")

    out = command.render("", shell_runner=runner)
    assert 'status="error"' in out
    assert "denied" in out
