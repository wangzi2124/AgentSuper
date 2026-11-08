"""G-15: unified diff patch tool without shell."""

from __future__ import annotations

from execution.filesystem import VirtualFileSystem
from execution.tools.file_tools import make_file_tools


def _tools(tmp_path):
    (tmp_path / "a.txt").write_text("a\nb\n", encoding="utf-8")
    return make_file_tools(VirtualFileSystem(root=tmp_path))


def test_apply_patch(tmp_path):
    tools = _tools(tmp_path)
    diff = "--- a.txt\n+++ a.txt\n@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d\n"
    out = tools["apply_patch"](diff)
    assert "已写入" in out
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a\nc\nd\n"


def test_patch_context_mismatch(tmp_path):
    tools = _tools(tmp_path)
    diff = "--- a.txt\n+++ a.txt\n@@ -1,2 +1,2 @@\n x\n-b\n+c\n"
    out = tools["apply_patch"](diff)
    assert "补丁应用失败" in out


def test_patch_rejects_escape(tmp_path):
    tools = _tools(tmp_path)
    out = tools["apply_patch"]("--- ../x\n+++ ../x\n@@ -1 +1 @@\n-a\n+b\n")
    assert "工作区内" in out
