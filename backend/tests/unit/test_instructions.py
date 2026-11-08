"""G-01: project instruction discovery, change detection, and budgeting."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config import settings
from prompts import instructions as mod
from prompts.assembly import compose_system
from prompts.instructions import InstructionResolver, get_instruction_resolver, reset_instruction_resolver


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    reset_instruction_resolver()
    monkeypatch.setattr(settings, "instructions_enabled", True)
    monkeypatch.setattr(settings, "instructions", [])
    monkeypatch.setattr(settings, "instructions_max_chars", 16000)
    monkeypatch.setattr(settings, "instructions_max_tokens", 4000)
    yield
    reset_instruction_resolver()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discovery_order_and_dedup(tmp_path, monkeypatch):
    global_file = _write(tmp_path / "global" / "AGENTS.md", "GLOBAL RULES")
    monkeypatch.setattr(mod, "_global_candidates", lambda: [global_file])

    workspace = tmp_path / "repo" / "pkg"
    workspace.mkdir(parents=True)
    _write(tmp_path / "repo" / "AGENTS.md", "REPO RULES")
    _write(workspace / "AGENTS.md", "PKG RULES")
    _write(workspace / "extra.md", "EXTRA RULES")
    monkeypatch.setattr(settings, "instructions", ["extra.md"])

    blocks = InstructionResolver().resolve(workspace, "deepseek/x")
    locations = [b.source.location for b in blocks]
    assert [Path(loc).name for loc in locations] == ["AGENTS.md", "AGENTS.md", "AGENTS.md", "extra.md"]
    kinds = [b.source.kind for b in blocks]
    assert kinds == ["global", "project", "project", "config"]
    assert "GLOBAL RULES" in blocks[0].content
    assert "REPO RULES" in blocks[1].content
    assert "PKG RULES" in blocks[2].content
    assert "EXTRA RULES" in blocks[3].content


def test_duplicate_content_is_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_global_candidates", lambda: [])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write(workspace / "AGENTS.md", "SAME")
    _write(workspace / "dup.md", "SAME")
    monkeypatch.setattr(settings, "instructions", ["dup.md"])
    blocks = InstructionResolver().resolve(workspace)
    assert len(blocks) == 1


def test_nested_loaded_on_demand(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_global_candidates", lambda: [])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write(workspace / "AGENTS.md", "ROOT")
    sub = workspace / "src" / "ui"
    sub.mkdir(parents=True)
    _write(sub / "AGENTS.md", "NESTED")

    resolver = InstructionResolver()
    first = resolver.resolve(workspace)
    assert [b.content for b in first] == ["ROOT"]

    resolver.note_access(sub / "button.ts", workspace)
    second = resolver.resolve(workspace)
    assert [b.content for b in second] == ["ROOT", "NESTED"]
    assert second[-1].source.kind == "nested"


def test_mtime_change_is_reloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_global_candidates", lambda: [])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = _write(workspace / "AGENTS.md", "v1")

    resolver = InstructionResolver()
    assert "v1" in resolver.resolve(workspace)[0].content

    target.write_text("v2", encoding="utf-8")
    os.utime(target, (target.stat().st_atime + 10, target.stat().st_mtime + 10))
    assert "v2" in resolver.resolve(workspace)[0].content


def test_deleted_file_disappears(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_global_candidates", lambda: [])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = _write(workspace / "AGENTS.md", "gone soon")

    resolver = InstructionResolver()
    assert resolver.resolve(workspace)
    target.unlink()
    assert resolver.resolve(workspace) == []
    described = resolver.describe(workspace)
    assert described == []


def test_url_timeout_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_global_candidates", lambda: [])
    monkeypatch.setattr(settings, "instructions", ["https://example.invalid/rules.md"])

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise TimeoutError("timed out")

    monkeypatch.setattr(mod.urllib.request, "urlopen", _boom)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    resolver = InstructionResolver()
    assert resolver.resolve(workspace) == []
    described = resolver.describe(workspace)
    assert described[0]["loaded"] is False
    assert described[0]["skipped_reason"] == "url_failed"


def test_overlong_instruction_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_global_candidates", lambda: [])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write(workspace / "big.md", "x" * 500)
    monkeypatch.setattr(settings, "instructions", ["big.md"])
    monkeypatch.setattr(settings, "instructions_max_chars", 40)
    monkeypatch.setattr(settings, "instructions_max_tokens", 0)

    blocks = InstructionResolver().resolve(workspace)
    assert len(blocks) == 1
    assert len(blocks[0].content) <= 40
    assert "[指令已截断]" in blocks[0].content


def test_disabled_returns_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "instructions_enabled", False)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _write(workspace / "AGENTS.md", "ROOT")
    assert InstructionResolver().resolve(workspace) == []


def test_compose_system_puts_instructions_after_skills():
    text = compose_system(
        "file_agent",
        head_override="HEAD",
        extra="SKILLS",
        instructions="RULES",
    )
    assert text.index("HEAD") < text.index("SKILLS") < text.index("RULES")
    assert "[项目指令]" in text


def test_singleton_is_resettable():
    first = get_instruction_resolver()
    assert first is get_instruction_resolver()
    reset_instruction_resolver()
    assert get_instruction_resolver() is not first
