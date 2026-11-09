"""Formatter hook fires for ``append_file`` and ``apply_patch`` (P0-6)."""

from __future__ import annotations

import execution.tools.file_tools as file_tools
from execution.filesystem import VirtualFileSystem
from execution.tools.file_tools import append_file
from execution.tools.patch_tools import make_patch_tools


def _record(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_after_mutation(vfs, path):
        calls.append((vfs.root, path))

    monkeypatch.setattr(file_tools, "_after_mutation", fake_after_mutation)
    return calls


def test_append_file_runs_formatter(monkeypatch, tmp_path):
    calls = _record(monkeypatch)
    vfs = VirtualFileSystem(root=str(tmp_path))
    out = append_file(vfs, "a.py", "print(1)\n")
    assert out == "已追加 9 字符到 a.py"
    assert calls == [(vfs.root, "a.py")]


def test_apply_patch_runs_formatter(monkeypatch, tmp_path):
    calls = _record(monkeypatch)
    vfs = VirtualFileSystem(root=str(tmp_path))
    vfs.write_file("a.txt", "alpha\n")
    diff = "--- a.txt\n+++ a.txt\n@@ -1,1 +1,1 @@\n-alpha\n+beta\n"
    out = make_patch_tools(vfs)["apply_patch"](diff)
    assert not out.startswith("错误：")
    assert calls == [(vfs.root, "a.txt")]
    assert vfs.read_file("a.txt") == "beta\n"


def test_apply_patch_failure_does_not_run_formatter(monkeypatch, tmp_path):
    calls = _record(monkeypatch)
    vfs = VirtualFileSystem(root=str(tmp_path))
    vfs.write_file("a.txt", "alpha\n")
    diff = "--- a.txt\n+++ a.txt\n@@ -1,1 +1,1 @@\n-nope\n+beta\n"
    out = make_patch_tools(vfs)["apply_patch"](diff)
    assert out.startswith("错误：")
    assert calls == []
