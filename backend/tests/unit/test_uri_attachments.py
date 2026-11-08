"""G-16: file URI parsing and line ranges."""

from __future__ import annotations

import pytest

from execution.tools.file_tools import make_file_tools
from execution.uri import parse_file_uri, slice_lines
from execution.filesystem import VirtualFileSystem


def test_parse_file_uri_with_range():
    parsed = parse_file_uri("file:///tmp/a.txt#L2-L3")
    assert parsed is not None
    assert parsed.path.endswith("a.txt")
    assert (parsed.start, parsed.end) == (2, 3)


def test_parse_file_uri_plain_range():
    parsed = parse_file_uri("file:///tmp/a.txt#5-6")
    assert parsed.start == 5 and parsed.end == 6


def test_parse_file_uri_rejects_bad_range():
    with pytest.raises(ValueError):
        parse_file_uri("file:///tmp/a.txt#nope")


def test_non_uri_passthrough():
    assert parse_file_uri("a.txt") is None


def test_slice_lines():
    text = "a\nb\nc\nd"
    assert slice_lines(text, 2, 3) == "b\nc"


def test_read_file_uri_in_workspace(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("l1\nl2\nl3\n", encoding="utf-8")
    vfs = VirtualFileSystem(root=tmp_path)
    tools = make_file_tools(vfs)
    out = tools["read_file"](target.as_uri() + "#L2-L3")
    assert out.strip() == "l2\nl3"
