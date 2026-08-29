"""filesystem/core.py + filesystem/fsutil.py 路径助手 / ScanCache / 文件操作测试。

core.py 是历史路径工具(opencode normalizePaths 等)；fsutil.py 是其
opencode 命名空间的完整版本；两者共享 tmp 目录无外部依赖。
"""
import os
from pathlib import Path

import pytest

from app.filesystem import core
from app.filesystem import fsutil
from app.filesystem.core import ScanCache


@pytest.fixture
def tree(tmp_path):
    base = tmp_path / "tree"
    (base / "a" / "b").mkdir(parents=True)
    (base / "marker.txt").write_text("m", encoding="utf-8")
    (base / "a" / "x.ini").write_text("x", encoding="utf-8")
    return base


# ---------------------------------------------------------------------------
# core.py 路径助手
# ---------------------------------------------------------------------------


class TestCorePaths:
    def test_normalize_absolute_resolved(self, tmp_path):
        assert core.normalize_path(tmp_path) == str(tmp_path.resolve())

    def test_normalize_expanduser(self):
        out = core.normalize_path("~")
        assert os.path.isabs(out)
        assert Path(out).exists()

    def test_overlaps_equal(self, tmp_path):
        p = tmp_path / "x"
        assert core.overlaps(str(p), str(p))

    def test_overlaps_parent_child(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "a" / "b"
        assert core.overlaps(str(a), str(b))
        assert core.overlaps(str(b), str(a))

    def test_overlaps_sibling_false(self, tmp_path):
        assert not core.overlaps(str(tmp_path / "a"), str(tmp_path / "b"))

    def test_contains_same(self, tmp_path):
        p = tmp_path / "x"
        assert core.contains(str(p), str(p))

    def test_contains_child(self, tmp_path):
        assert core.contains(str(tmp_path), str(tmp_path / "a" / "b"))
        assert core.contains(str(tmp_path / "a"), str(tmp_path / "a" / "b"))

    def test_contains_outside_false(self, tmp_path):
        assert not core.contains(str(tmp_path), str(tmp_path.parent / "other"))

    def test_up_default_one(self, tmp_path):
        f = tmp_path / "a" / "b" / "c.txt"
        assert core.up(str(f)) == str((tmp_path / "a" / "b").resolve())

    def test_up_levels(self, tmp_path):
        f = tmp_path / "a" / "b" / "c.txt"
        assert core.up(str(f), 2) == str((tmp_path / "a").resolve())
        assert core.up(str(f), 0) == str((tmp_path / "a" / "b" / "c.txt").resolve())
        assert core.up(str(f), -3) == str((tmp_path / "a" / "b" / "c.txt").resolve())

    def test_find_up_finds_marker(self, tree):
        start = tree / "a" / "b"
        assert core.find_up("marker.txt", start) == str(tree.resolve() / "marker.txt")

    def test_find_up_str_or_iterable(self, tree):
        start = tree / "a"
        assert core.find_up("x.ini", start) == str((tree / "a" / "x.ini").resolve())
        assert core.find_up(["marker.txt"], start) == str((tree / "marker.txt").resolve())

    def test_find_up_not_found(self, tree):
        assert core.find_up("zzz.nope", tree) is None

    def test_find_up_missing_dir(self, tmp_path):
        assert core.find_up("anything.txt", tmp_path / "gone") is None

    def test_glob_up_sorted_files_only(self, tree):
        (tree / "a" / "b" / "outer.log").write_text("1", encoding="utf-8")
        (tree / "top.log").write_text("2", encoding="utf-8")
        hits = [Path(h) for h in core.glob_up("*.log", tree / "a" / "b")]
        assert (tree / "a" / "b" / "outer.log").resolve() in hits
        assert (tree / "top.log").resolve() in hits
        assert all(h.is_file() for h in hits)

    def test_glob_up_max_levels_caps(self, tree):
        (tree / "a" / "b" / "outer.log").write_text("1", encoding="utf-8")
        (tree / "top.log").write_text("2", encoding="utf-8")
        hits = list(core.glob_up("*.log", tree / "a" / "b", max_levels=2))
        assert [Path(h).name for h in hits] == ["outer.log"]
        hits0 = list(core.glob_up("*.log", tree / "a" / "b", max_levels=0))
        assert hits0 == []


# ---------------------------------------------------------------------------
# ScanCache
# ---------------------------------------------------------------------------


class TestScanCache:
    def test_list_dir_sorted_case_insensitive(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        (d / "B.txt").write_text("1", encoding="utf-8")
        (d / "a.txt").write_text("2", encoding="utf-8")
        entries = ScanCache().list_dir(d)
        assert [e.name for e in entries] == ["a.txt", "B.txt"]
        assert entries[0].type == "file"
        assert entries[0].absolute == str((d / "a.txt").resolve())

    def test_list_dir_dir_type(self, tmp_path):
        d = tmp_path / "d"
        (d / "sub").mkdir(parents=True)
        (d / "f.txt").write_text("x", encoding="utf-8")
        entries = ScanCache().list_dir(d)
        by_name = {e.name: e for e in entries}
        assert by_name["sub"].type == "dir"
        assert by_name["f.txt"].type == "file"

    def test_cache_hit_returns_same_object(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        (d / "a.txt").write_text("x", encoding="utf-8")
        ns = 1_000_000_000
        os.utime(d, ns=(ns, ns))
        sc = ScanCache()
        first = sc.list_dir(d)
        os.utime(d, ns=(ns, ns))
        second = sc.list_dir(d)
        assert second is first

    def test_cache_refresh_on_mtime_change(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        (d / "a.txt").write_text("x", encoding="utf-8")
        sc = ScanCache()
        sc.list_dir(d)
        ns0 = d.stat().st_mtime_ns
        (d / "b.txt").write_text("y", encoding="utf-8")
        os.utime(d, ns=(ns0 + 1_000_000_000, ns0 + 1_000_000_000))
        names = [e.name for e in sc.list_dir(d)]
        assert names == ["a.txt", "b.txt"]

    def test_force_refresh(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        (d / "a.txt").write_text("x", encoding="utf-8")
        sc = ScanCache()
        sc.list_dir(d)
        (d / "b.txt").write_text("y", encoding="utf-8")
        names = [e.name for e in sc.list_dir(d, force_refresh=True)]
        assert names == ["a.txt", "b.txt"]

    def test_invalidate_removes_key_and_parent(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        sc = ScanCache()
        sc.list_dir(d)
        assert str(d.resolve()) in sc._entries
        sc.invalidate(d)
        assert str(d.resolve()) not in sc._entries
        assert str(d.parent.resolve()) not in sc._entries

    def test_invalidate_missing_no_error(self, tmp_path):
        ScanCache().invalidate(tmp_path / "nope")

    def test_clear(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        sc = ScanCache()
        sc.list_dir(d)
        sc.clear()
        assert sc._entries == {}

    def test_missing_dir_empty(self, tmp_path):
        assert ScanCache().list_dir(tmp_path / "gone") == []

    def test_ignored_checker(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        (d / "secret.txt").write_text("s", encoding="utf-8")
        (d / "ok.txt").write_text("o", encoding="utf-8")

        def checker(p: Path, is_dir: bool) -> bool:
            return p.name == "secret.txt"

        entries = ScanCache(ignored_checker=checker).list_dir(d)
        by_name = {e.name: e for e in entries}
        assert by_name["secret.txt"].ignored is True
        assert by_name["ok.txt"].ignored is False

    def test_ignored_checker_oserror_means_not_ignored(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        (d / "a.txt").write_text("x", encoding="utf-8")

        def checker(p: Path, is_dir: bool) -> bool:
            raise OSError("boom")

        entries = ScanCache(ignored_checker=checker).list_dir(d)
        assert entries[0].ignored is False


# ---------------------------------------------------------------------------
# fsutil.py 路径助手
# ---------------------------------------------------------------------------


class TestFSUtilPathHelpers:
    def test_normalize_path(self, tmp_path):
        assert fsutil.normalize_path(tmp_path) == str(tmp_path.resolve())
        assert os.path.isabs(fsutil.normalize_path("~"))

    def test_windows_path_drive_unchanged(self):
        assert fsutil.windows_path("C:/x/y.txt") == r"C:\x\y.txt"

    def test_windows_path_cygwin(self):
        assert fsutil.windows_path("/c/Users/me") == r"C:\Users\me"

    def test_windows_path_slash_replace(self):
        assert fsutil.windows_path("/var/log") == r"\var\log"

    def test_resolve(self, tmp_path):
        assert fsutil.resolve(tmp_path / "a" / ".." / "a") == str((tmp_path / "a").resolve())

    def test_mime_type_known_and_unknown(self):
        assert fsutil.mime_type("x.png") == "image/png"
        assert fsutil.mime_type("x.MD") == "text/markdown"
        assert fsutil.mime_type("x.zzz") == "application/octet-stream"

    def test_normalize_path_pattern(self):
        assert fsutil.normalize_path_pattern("a\\b/") == "a/b"
        assert fsutil.normalize_path_pattern(".") == "."

    def test_glob_match_absolute_and_relative(self, tmp_path):
        target = tmp_path / "src" / "main.py"
        # Windows 反斜杠绝对路径不匹配 POSIX glob；归一化为正斜杠再断言
        posix = str(target).replace("\\", "/")
        assert fsutil.glob_match("**/*.py", posix) is True
        assert fsutil.glob_match("*.md", posix) is False

    def test_contains_overlaps(self, tmp_path):
        assert fsutil.contains(str(tmp_path), str(tmp_path / "x"))
        assert not fsutil.contains(str(tmp_path / "x"), str(tmp_path))
        assert fsutil.overlaps(str(tmp_path), str(tmp_path / "x"))
        assert fsutil.overlaps(str(tmp_path / "x"), str(tmp_path))

    def test_up_and_glob_up(self, tree):
        f = tree / "a" / "b" / "c.txt"
        assert fsutil.up(str(f), 1) == str((tree / "a" / "b").resolve())
        assert fsutil.find_up("marker.txt", tree / "a" / "b") == str((tree / "marker.txt").resolve())
        # max_levels 限制向上级数，避免扫到用户目录的 *.ini
        assert [Path(h).name for h in fsutil.glob_up("*.ini", tree / "a" / "b", max_levels=2)] == ["x.ini"]

    def test_direntry(self):
        e = fsutil.DirEntry("a", "file")
        assert e.to_dict() == {"name": "a", "type": "file"}
        assert fsutil.DirEntry("d", "dir").type == "dir"


# ---------------------------------------------------------------------------
# fsutil.py 文件操作
# ---------------------------------------------------------------------------


class TestFSUtilFileOps:
    def test_is_dir_is_file_exists(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        f = d / "a.txt"
        f.write_text("x", encoding="utf-8")
        assert fsutil.is_dir(str(d))
        assert not fsutil.is_dir(str(f))
        assert fsutil.is_file(str(f))
        assert fsutil.is_file(str(d)) is False
        assert fsutil.exists(str(f))
        assert not fsutil.exists(str(d / "nope"))

    def test_read_file_string(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("héllo", encoding="utf-8")
        assert fsutil.read_file_string(str(f)) == "héllo"

    def test_read_directory_entries_sorted(self, tmp_path):
        d = tmp_path / "d"
        (d / "B").mkdir(parents=True)
        (d / "a.txt").write_text("x", encoding="utf-8")
        entries = fsutil.read_directory_entries(str(d))
        assert [(e.name, e.type) for e in entries] == [("a.txt", "file"), ("B", "dir")]

    def test_read_directory_entries_missing(self, tmp_path):
        assert fsutil.read_directory_entries(str(tmp_path / "gone")) == []

    def test_ensure_dir(self, tmp_path):
        p = tmp_path / "a" / "b" / "c"
        fsutil.ensure_dir(str(p))
        assert p.is_dir()

    def test_write_with_dirs_str_and_bytes(self, tmp_path):
        p = tmp_path / "deep" / "file.txt"
        fsutil.write_with_dirs(str(p), "text")
        assert p.read_text(encoding="utf-8") == "text"
        bp = tmp_path / "deep" / "blob.bin"
        fsutil.write_with_dirs(str(bp), b"\x00\x01")
        assert bp.read_bytes() == b"\x00\x01"

    def test_json_roundtrip(self, tmp_path):
        p = tmp_path / "data" / "cfg.json"
        fsutil.write_json(str(p), {"a": [1, 2], "zh": "你好"})
        assert fsutil.read_json(str(p)) == {"a": [1, 2], "zh": "你好"}
        raw = p.read_text(encoding="utf-8")
        assert "你好" in raw
        assert raw.startswith("{")

    def test_read_json_defaults(self, tmp_path):
        missing = tmp_path / "nope.json"
        assert fsutil.read_json(str(missing), "fallback") == "fallback"
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert fsutil.read_json(str(bad), "fallback") == "fallback"