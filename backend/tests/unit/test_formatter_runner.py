"""G-03 external formatter runner tests."""
from __future__ import annotations

import sys
from pathlib import Path

from config import settings
from execution.formatters.service import FormatterService


def _script(tmp_path: Path, body: str, stem: str = "fmt") -> Path:
    path = tmp_path / (stem + ".py")
    path.write_text("import sys, pathlib\n" + body, encoding="utf-8")
    return path


def _spec(script: Path, name: str = "fake", ext: str = ".txt", timeout: float = 10.0) -> dict:
    return {
        "name": name,
        "command": [sys.executable, str(script)],
        "extensions": [ext],
        "timeout": timeout,
    }


def test_formatter_changes_file(tmp_path, monkeypatch):
    script = _script(tmp_path, "p=pathlib.Path(sys.argv[1]); p.write_text('FORMATTED', encoding='utf-8')\n")
    monkeypatch.setattr(settings, "formatter", {"items": [_spec(script)]})
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")

    result = FormatterService.from_settings().apply(target, tmp_path)
    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "FORMATTED"


def test_formatter_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "formatter", False)
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    result = FormatterService.from_settings().apply(target, tmp_path)
    assert result.status == "skipped"


def test_first_success_stops(tmp_path, monkeypatch):
    first = _script(tmp_path, "p=pathlib.Path(sys.argv[1]); p.write_text('FIRST', encoding='utf-8')\n", "first")
    second = _script(tmp_path, "p=pathlib.Path(sys.argv[1]); p.write_text('SECOND', encoding='utf-8')\n", "second")
    monkeypatch.setattr(
        settings,
        "formatter",
        {"items": [_spec(first, "one"), _spec(second, "two")]},
    )
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    result = FormatterService.from_settings().apply(target, tmp_path)
    assert result.formatter == "one"
    assert target.read_text(encoding="utf-8") == "FIRST"


def test_failure_falls_back_to_next(tmp_path, monkeypatch):
    bad = _script(tmp_path, "import sys; sys.exit(2)\n", "bad")
    good = _script(tmp_path, "p=pathlib.Path(sys.argv[1]); p.write_text('OK', encoding='utf-8')\n", "good")
    monkeypatch.setattr(
        settings,
        "formatter",
        {"items": [_spec(bad, "bad"), _spec(good, "good")]},
    )
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    result = FormatterService.from_settings().apply(target, tmp_path)
    assert result.status == "success"
    assert result.formatter == "good"


def test_disabled_named_formatter_is_skipped(tmp_path, monkeypatch):
    script = _script(tmp_path, "p=pathlib.Path(sys.argv[1]); p.write_text('X', encoding='utf-8')\n")
    monkeypatch.setattr(settings, "formatter", {"items": [_spec(script)], "disabled": ["fake"]})
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    assert FormatterService.from_settings().apply(target, tmp_path).status == "skipped"

