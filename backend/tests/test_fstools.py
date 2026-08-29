"""Unit tests for the fstools file-op layer (writer/reader/search/patch/common/workspace).

No git worktree, no permission manager, no real project context: module-level
_resolve/_ensure_safe/_is_read_allowed/_scan_cache are monkeypatched so every
operation lands in a tmp work dir.
"""
import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tools.fstools import writer, reader, search, patch, common
from app.tools.fstools.workspace import _ensure_safe, _resolve, _is_read_allowed


class _StubScan:
    def __init__(self, root: Path):
        self.root = root

    def invalidate(self, *a, **k):
        return None

    def list_dir(self, target: Path):
        rows = []
        if target.is_dir():
            for e in sorted(os.scandir(target), key=lambda x: x.name.lower()):
                st = e.stat(follow_symlinks=False)
                rows.append(SimpleNamespace(
                    name=e.name,
                    type="dir" if e.is_dir(follow_symlinks=False) else "file",
                    path=e.path,
                    ignored=False,
                    stat=lambda: st,
                ))
        return rows


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    for mod in (writer, reader, search, patch):
        monkeypatch.setattr(mod, "_resolve", lambda p, _r=root: (_r / p).resolve())
        monkeypatch.setattr(mod, "_ensure_safe", lambda *a, **k: None)
    for mod in (writer, reader, patch):
        monkeypatch.setattr(mod, "_scan_cache", _StubScan(root))
    monkeypatch.setattr(search, "_is_read_allowed", lambda p: True)
    monkeypatch.setattr(search, "_gitignore_matcher", lambda: None)
    return root


# ---------------------------------------------------------------------------
# common
# ---------------------------------------------------------------------------

class TestCommon:
    def test_env_and_unwrap(self):
        e = common._env("x", "hello", size=3)
        assert e == {"title": "x", "metadata": {"size": 3}, "output": "hello"}
        assert common.unwrap(e) == "hello"
        assert common.unwrap("plain") == "plain"
        assert common.unwrap(42) == "42"

    def test_coerce_bool(self):
        assert common._coerce_bool(True) is True
        assert common._coerce_bool("yes") is True
        assert common._coerce_bool("1") is True
        assert common._coerce_bool("ON") is True
        assert common._coerce_bool("no") is False
        assert common._coerce_bool(None, default=True) is True
        assert common._coerce_bool(1) is True

    def test_coerce_int(self):
        assert common._coerce_int("12", 0) == 12
        assert common._coerce_int(True, 5) == 5
        assert common._coerce_int("abc", 7) == 7
        assert common._coerce_int(None, 7) == 7

    def test_ext_constants(self):
        assert ".png" in common._MULTIMODAL_EXTS
        assert common._MIME_MAP[".pdf"] == "application/pdf"
        assert ".exe" in common._BINARY_EXTS
        assert common.MAX_BYTES == 50 * 1024


# ---------------------------------------------------------------------------
# writer pure helpers
# ---------------------------------------------------------------------------

class TestWriterHelpers:
    def test_detect_line_ending(self):
        assert writer._detect_line_ending("a\nb") == "\n"
        assert writer._detect_line_ending("a\r\nb") == "\r\n"
        assert writer._detect_line_ending("a\r\nb\n") == "\n"

    def test_normalize_and_convert(self):
        assert writer._normalize_line_endings("a\r\nb") == "a\nb"
        assert writer._convert_line_ending("a\nb", "\n") == "a\nb"
        assert writer._convert_line_ending("a\nb", "\r\n") == "a\r\nb"

    def test_read_write_raw(self, tmp_path):
        p = tmp_path / "f.txt"
        writer._write_text_raw(p, "abc\r\n", has_bom=True)
        text, has_bom = writer._read_text_raw(p)
        assert text == "abc\r\n" and has_bom is True
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")
        writer._write_text_raw(p, "\ufeffzed")
        t2, bom2 = writer._read_text_raw(p)
        assert bom2 is True and t2 == "zed"

    def test_levenshtein(self):
        assert writer._edit_levenshtein("", "abc") == 3
        assert writer._edit_levenshtein("abc", "") == 3
        assert writer._edit_levenshtein("kitten", "sitting") <= 5

    def test_line_positions(self):
        lines = ["aa", "bb", "cc", "dd"]
        start, end = writer._edit_line_positions(lines, 1, 2)
        assert lines is not None
        assert (start, end) == (3, 8)

    def test_simple_replacer(self):
        assert list(writer._edit_simple_replacer("x", "find")) == ["find"]

    def test_line_trimmed_replacer(self):
        out = list(writer._edit_line_trimmed_replacer("a\n  b\nc", "  a\nb\n  c"))
        assert out == ["a\n  b\nc"]

    def test_edit_replace_single(self):
        assert writer._edit_replace("hello world", "world", "there") == "hello there"

    def test_edit_replace_not_found(self):
        with pytest.raises(ValueError, match="oldString not found"):
            writer._edit_replace("abc", "zzz", "q")

    def test_edit_replace_multi(self):
        with pytest.raises(ValueError, match="multiple matches"):
            writer._edit_replace("a b a", "a", "x")

    def test_edit_replace_all(self):
        assert writer._edit_replace("a b a", "a", "x", replace_all=True) == "x b x"

    def test_edit_replace_same(self):
        with pytest.raises(ValueError):
            writer._edit_replace("ab", "ab", "ab")


# ---------------------------------------------------------------------------
# writer tools
# ---------------------------------------------------------------------------

class TestWriterTools:
    def test_write_create_and_overwrite(self, ws):
        r = writer.tool_write_file("a.txt", "hello", overwrite=False)
        assert r["metadata"]["action"] == "created" and ws.joinpath("a.txt").exists()
        r2 = writer.tool_write_file("a.txt", "nope")
        assert r2["metadata"]["error"] is True
        r3 = writer.tool_write_file("a.txt", "hi", overwrite=True)
        assert r3["metadata"]["action"] == "overwritten"
        assert ws.joinpath("a.txt").read_text(encoding="utf-8") == "hi"

    def test_write_nested_dir(self, ws):
        writer.tool_write_file("sub/deep/f.txt", "x")
        assert ws.joinpath("sub/deep/f.txt").is_file()

    def test_write_error_envelope(self, ws, monkeypatch):
        def boom(path, text, has_bom=False):
            raise OSError("disk full")
        monkeypatch.setattr(writer, "_write_text_raw", boom)
        assert writer.tool_write_file("x.txt", "d")["metadata"]["error"] is True

    def test_append_create_and_append(self, ws):
        assert "Created" in writer.tool_append_file("app.txt", "one")["output"]
        assert "Appended to" in writer.tool_append_file("app.txt", "two")["output"]
        assert ws.joinpath("app.txt").read_text(encoding="utf-8") == "onetwo"

    def test_append_error_envelope(self, ws, monkeypatch):
        def bad_open(*a, **k):
            raise OSError("boom")
        monkeypatch.setattr(writer, "open", bad_open, raising=False)
        monkeypatch.setattr(writer, "_scan_cache", _StubScan(ws))
        assert writer.tool_append_file("a.txt", "d")["metadata"]["error"] is True

    def test_edit_file_not_found(self, ws):
        assert "not found" in writer.tool_edit_file("missing.txt", "a", "b")["output"]

    def test_edit_file_basic(self, ws):
        writer.tool_write_file("e.txt", "line one\nline two\n")
        r = writer.tool_edit_file("e.txt", "line one", "LINE ONE")
        assert "Edited" in r["output"]
        assert ws.joinpath("e.txt").read_text(encoding="utf-8") == "LINE ONE\nline two\n"

    def test_edit_file_unmatched(self, ws):
        writer.tool_write_file("e2.txt", "abc")
        r = writer.tool_edit_file("e2.txt", "zzz", "q")
        assert "Error" in r["output"]

    def test_edit_file_empty_oldstring_rejected(self, ws):
        """[C5 修复] 空 old_string 不得触发整文件替换（防 LLM 误发空字符串清空文件）。"""
        writer.tool_write_file("e3.txt", "abc")
        r = writer.tool_edit_file("e3.txt", "", ">> ")
        assert "old_string 不能为空" in r["output"]
        assert r["metadata"]["error"] is True
        assert ws.joinpath("e3.txt").read_text(encoding="utf-8") == "abc"  # 文件未被改动

    def test_edit_file_crlf_preserved(self, ws):
        p = ws.joinpath("crlf.txt")
        p.write_text("a\r\nb\r\n", newline="")
        writer.tool_edit_file("crlf.txt", "a", "A")
        assert p.read_bytes() == b"A\r\nb\r\n"

    def test_delete_file_and_dir(self, ws):
        f = ws / "d.txt"
        f.write_text("x")
        assert "Deleted" in writer.tool_delete_file("d.txt")["output"]
        assert not f.exists()
        d = ws / "emptydir"
        d.mkdir()
        out = writer.tool_delete_file("emptydir")
        assert out["metadata"]["kind"] == "dir" and not d.exists()
        assert "not found" in writer.tool_delete_file("nonexistent")["output"]

    def test_delete_nonempty_dir_error(self, ws):
        d = ws / "full"
        d.mkdir()
        (d / "f.txt").write_text("x")
        out = writer.tool_delete_file("full")
        assert out["metadata"]["error"] is True

    def test_rename_file(self, ws):
        (ws / "r.txt").write_text("data")
        out = writer.tool_rename_file("r.txt", "moved.txt")
        assert not (ws / "r.txt").exists() and (ws / "moved.txt").exists()
        assert "Renamed" in out["output"]
        assert "source not found" in writer.tool_rename_file("zz.txt", "y.txt")["output"]
        (ws / "again.txt").write_text("z")
        assert "destination already exists" in writer.tool_rename_file("moved.txt", "again.txt")["output"]


# ---------------------------------------------------------------------------
# reader
# ---------------------------------------------------------------------------

class TestReader:
    def test_is_binary(self, ws):
        assert reader._is_binary(ws / "a.bin") is True
        b = ws / "b.dat"
        b.write_bytes(b"x\x00y")
        assert reader._is_binary(b) is True
        nb = ws / "c.txt"
        nb.write_text("just text", encoding="utf-8")
        assert reader._is_binary(nb) is False

    def test_read_binary_ext_error(self, ws):
        p = ws / "data.bin"
        p.write_bytes(b"\x01\x02")
        out = reader.tool_read_file("data.bin")
        assert "binary" in out["output"]

    def test_read_multimodal_base64(self, ws):
        p = ws / "img.png"
        p.write_bytes(b"\x89PNG-fake")
        out = reader.tool_read_file("img.png")
        assert out["metadata"]["mime"] == "image/png"
        assert "base64" in out["output"]

    def test_read_text_paging(self, ws):
        p = ws / "lines.txt"
        p.write_text("".join(f"line{i}\n" for i in range(10)), encoding="utf-8")
        out = reader.tool_read_file("lines.txt", offset=3, limit=2)
        body = out["output"]
        assert "3: line2" in body and "4: line3" in body
        assert "Showing lines 3-4 of 10" in body

    def test_read_byte_cap_truncates(self, ws):
        big = "x" * 100 + "\n"
        (ws / "big.txt").write_text(big * 600, encoding="utf-8")
        out = reader.tool_read_file("big.txt", limit=5000)
        assert out["metadata"]["truncated"] is True
        assert "Output capped at 50 KB" in out["output"]

    def test_read_offset_out_of_range(self, ws):
        (ws / "few.txt").write_text("a\nb\n", encoding="utf-8")
        out = reader.tool_read_file("few.txt", offset=99)
        assert "out of range" in out["output"]

    def test_read_not_found_suggestion(self, ws):
        (ws / "report.txt").write_text("x")
        body = reader.tool_read_file("report.txt.backup")["output"]
        assert "Did you mean" in body
        assert "File not found" in reader.tool_read_file("nomatch.txt")["output"]

    def test_read_directory(self, ws):
        (ws / "adir").mkdir()
        (ws / "one.txt").write_text("1")
        out = reader.tool_read_file(".")
        assert "<type>directory</type>" in out["output"]
        assert "adir/" in out["output"]

    def test_ls_empty_and_rows(self, ws):
        empty = ws / "sub"
        empty.mkdir()
        out = reader.tool_ls("sub")
        assert out["output"] == "(empty)"
        (ws / "sub" / "f.txt").write_text("hi")
        (ws / "sub" / "dir2").mkdir()
        out2 = reader.tool_ls("sub")
        rows = out2["output"].splitlines()
        assert any(r.endswith("f.txt") and r.startswith("f") for r in rows)
        assert any(r.rstrip().endswith("dir2") for r in rows)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_glob_found_and_empty(self, ws):
        (ws / "a.py").write_text("x")
        (ws / "b.txt").write_text("y")
        out = search.tool_glob("*.py")
        assert "a.py" in out["output"] and out["metadata"]["total_matches"] == 1
        none = search.tool_glob("*.zzz")
        assert none["output"] == "No files found"

    def test_glob_non_directory_returns_error(self, ws):
        f = ws / "plain.txt"
        f.write_text("x")
        out = search.tool_glob("*", path="plain.txt")
        assert out["metadata"]["error"] is True
        assert "is not a directory" in out["output"]
        assert "undefined" not in out["output"]

    def test_glob_recent_first(self, ws):
        for i in range(3):
            (ws / f"file{i}.md").write_text("x")
        out = search.tool_glob("*.md")
        lines = out["output"].splitlines()
        assert len(lines) == 3
        t = [os.path.getmtime(ws / Path(l).name) for l in lines]
        assert t == sorted(t, reverse=True)

    def test_grep_found(self, ws):
        (ws / "g.txt").write_text("alpha\nbeta\nalpha again\n", encoding="utf-8")
        out = search.tool_grep("alpha")
        assert "Found 2 matches" in out["output"]
        assert "g.txt:" in out["output"]

    def test_grep_no_match(self, ws):
        (ws / "g2.txt").write_text("nothing", encoding="utf-8")
        out = search.tool_grep("zzzz")
        assert out["output"] == "No files found"

    def test_grep_count_and_files_only(self, ws):
        for i in range(2):
            (ws / f"c{i}.txt").write_text("hit here", encoding="utf-8")
        cnt = search.tool_grep("hit", count_only=True)
        assert "match(es)" in cnt["output"]
        flags = search.tool_grep("hit", files_only=True)
        assert "c0.txt" in flags["output"] and "c1.txt" in flags["output"]

    def test_grep_context(self, ws):
        (ws / "ctx.txt").write_text("aaa\nbbb\nccc", encoding="utf-8")
        out = search.tool_grep("bbb", context=1)
        assert "  > Line 2: bbb" in out["output"]
        assert "   Line 1: aaa" in out["output"]
        assert "   Line 3: ccc" in out["output"]

    def test_grep_multimodal_skipped(self, ws):
        (ws / "pic.png").write_bytes(b"\x89PNG")
        out = search.tool_grep("PNG")
        assert out["output"] == "No files found"

    def test_grep_include_filter(self, ws):
        (ws / "a.py").write_text("findme", encoding="utf-8")
        (ws / "a.txt").write_text("findme", encoding="utf-8")
        out = search.tool_grep("findme", include="*.py")
        assert "No files found" not in out["output"]


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------

class TestPatch:
    def test_split_sections(self):
        text = (
            "*** Begin Patch\n"
            "*** Add File: a.txt\n"
            "+hello\n"
            "*** Update File: b.txt\n"
            "@@ anchor\n"
            "-old\n"
            "+new\n"
            "*** Delete File: c.txt\n"
            "+ignored\n"
            "*** End Patch\n"
        )
        sections = patch._patch_split_sections(text)
        assert sections[0][0] == "add" and sections[0][1] == "a.txt"
        assert sections[1][0] == "update"
        assert sections[2][0] == "delete"

    def test_add_body(self):
        assert patch._patch_add_body("+a\n+b\n") == "a\nb\n"
        assert patch._patch_add_body("+a\n+b") == "a\nb\n"

    def test_update_hunks(self):
        hunks = patch._patch_update_hunks("@@ x\n-old\n+new\n context\n")
        assert hunks == [("", ""), ("old\ncontext", "new\ncontext")]

    def test_apply_hunks(self):
        content = "one\ntwo\nthree"
        out = patch._patch_apply_hunks(content, [("two", "TWO")])
        assert out == "one\nTWO\nthree"

    def test_apply_hunks_append(self):
        out = patch._patch_apply_hunks("a\nb", [("", "c")])
        assert out == "a\nb\nc" or out == "a\nbc"

    def test_apply_hunks_trimmed_fallback(self):
        content = "aa\n  indented\nbb"
        out = patch._patch_apply_hunks(content, [(" aa\n indented\n bb", "aa\nCHANGED\nbb")])
        assert "CHANGED" in out

    def test_apply_hunks_not_found(self):
        with pytest.raises(ValueError):
            patch._patch_apply_hunks("aaa", [("bbb", "c")])

    def test_apply_patch_add_update_delete(self, ws):
        patch_text = (
            "*** Begin Patch\n"
            "*** Add File: n.txt\n"
            "+first line\n"
            "+second line\n"
            "*** Update File: n.txt\n"
            "@@ anchor\n"
            "-first line\n"
            "+FIRST\n"
            "*** Delete File: n.txt\n"
            "*** End Patch\n"
        )
        out = patch.tool_apply_patch(patch_text)
        assert out["output"].startswith("Applied patch sequentially:\nA n.txt\nM n.txt\nD n.txt")

    def test_apply_patch_errors(self, ws):
        assert "required" in patch.tool_apply_patch("")["output"]
        assert "empty patch" in patch.tool_apply_patch("*** Begin Patch\n*** End Patch")["output"]
        exists = patch.tool_apply_patch("*** Begin Patch\n*** Add File: z.txt\n+x\n*** End Patch")["output"]
        assert "Applied patch" in exists
        dup = patch.tool_apply_patch("*** Begin Patch\n*** Add File: z.txt\n+y\n*** End Patch")["output"]
        assert "already exists" in dup
        missing = patch.tool_apply_patch("*** Begin Patch\n*** Delete File: nope.txt\n*** End Patch")["output"]
        assert "not found" in missing

    def test_apply_patch_partial_failure(self, ws):
        (ws / "p1.txt").write_text("data")
        text = (
            "*** Begin Patch\n"
            "*** Delete File: p1.txt\n"
            "*** Update File: missing.txt\n"
            "@@ x\n"
            "-a\n"
            "+b\n"
            "*** End Patch\n"
        )
        out = patch.tool_apply_patch(text)
        assert "partially applied" in out["output"]
        assert not (ws / "p1.txt").exists()

    def test_edit_replace_all_in_file(self, ws):
        writer.tool_write_file("rep.txt", "foo foo foo")
        r = writer.tool_edit_file("rep.txt", "foo", "bar", replace_all=True)
        assert "Edited" in r["output"]
        assert ws.joinpath("rep.txt").read_text(encoding="utf-8") == "bar bar bar"


# ---------------------------------------------------------------------------
# writer fuzzy replacers
# ---------------------------------------------------------------------------

class TestReplacers:
    def test_block_anchor_single(self):
        content = "aa\n" + "first\nmiddle\nlast\n" + "bb"
        out = list(writer._edit_block_anchor_replacer(content, "first\nx\nlast"))
        assert out, "expected a yield"
        assert out[0] == "first\nmiddle\nlast"

    def test_block_anchor_best_of_multi(self):
        content = "s\ninner\nlast\nmid\ns\ninner\nlast\ntail"
        out = list(writer._edit_block_anchor_replacer(content, "s\ninner\nlast"))
        assert out and out[0] == "s\ninner\nlast"

    def test_block_anchor_no_candidates(self):
        assert list(writer._edit_block_anchor_replacer("no anchors here", "a\nb\nc")) == []
        assert list(writer._edit_block_anchor_replacer("a\nb", "a\nb\nc")) == []

    def test_whitespace_normalized_single_line(self):
        out = list(writer._edit_whitespace_normalized_replacer("a   b", "a b"))
        assert out == ["a   b"]

    def test_whitespace_normalized_substring(self):
        out = list(writer._edit_whitespace_normalized_replacer("prefix a b suffix", "a b"))
        assert out == ["a b"]

    def test_whitespace_normalized_multiline(self):
        out = list(writer._edit_whitespace_normalized_replacer("x\na" + " " + "b\ny", "a b"))
        assert any("a b" == o or "a b" == o.strip() for o in out)

    def test_indentation_flexible(self):
        out = list(writer._edit_indentation_flexible_replacer("    a\n    b", "a\nb"))
        assert out == ["    a\n    b"]

    def test_escape_normalized(self):
        out = list(writer._edit_escape_normalized_replacer("a\nb", "a\\nb"))
        assert out and "a\nb" in out

    def test_trimmed_boundary(self):
        assert set(writer._edit_trimmed_boundary_replacer("  hello  ", " hello")) == {"hello", "  hello  "}

    def test_context_aware(self):
        content = "ctx1\ninner1\ninner2\nctx2\nmore"
        out = list(writer._edit_context_aware_replacer(content, "ctx1\ninnerX\ninner2\nctx2"))
        assert out and "inner1" in out[0]

    def test_context_aware_too_short(self):
        assert list(writer._edit_context_aware_replacer("a\nb", "a\nb")) == []

    def test_multi_occurrence(self):
        assert list(writer._edit_multi_occurrence_replacer("aba", "a")) == ["a", "a"]


# ---------------------------------------------------------------------------
# workspace
# ---------------------------------------------------------------------------

class TestWorkspace:
    def test_ensure_safe_allow(self, monkeypatch, tmp_path):
        import app.tools.fstools.workspace as ws_mod
        mgr = SimpleNamespace(check=lambda path, op: "allow")
        monkeypatch.setitem(ws_mod.__dict__, "get_perm_mgr", lambda: mgr)
        _ensure_safe(tmp_path / "p", "write")

    def test_resolve_relative_worktree(self, monkeypatch, ws):
        import app.tools.fstools.workspace as ws_mod
        monkeypatch.setitem(ws_mod.__dict__, "_workspace", lambda: ws)
        got = _resolve("sub/x.txt")
        assert got == (ws / "sub" / "x.txt").resolve()

    def test_is_read_allowed_false(self, monkeypatch):
        import app.tools.fstools.workspace as ws_mod
        mgr = SimpleNamespace(check=lambda path, op: "deny")
        monkeypatch.setitem(ws_mod.__dict__, "get_perm_mgr", lambda: mgr)
        assert _is_read_allowed(Path("x")) is False