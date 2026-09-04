"""filesystem 层其余模块测试：ripgrep/search(纯 Python 回退与 rg 分叉)、
shell、watcher、project、FileSystem 服务抽象、models 数据模型。

rg 二进制可能安装(仓库 bundled data/bin/rg.exe 或 PATH)；测试通过 monkeypatch
_rg_path/_RG_CANDIDATES / sys.modules['subprocess'] 同时覆盖"有 rg"与"无 rg"两条分支
(超时/退出码/JSON)。
"""
import mimetypes
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.filesystem import filesystem as fssvc
from app.filesystem import models as fsmodels
from app.filesystem import project as project_mod
from app.filesystem import ripgrep
from app.filesystem import search as search_mod
from app.filesystem import shell as shell_mod
from app.filesystem import watcher as watcher_mod
from app.filesystem.ripgrep import (
    FindInput,
    InternalRipgrepError,
    InvalidPatternError,
    MAX_RECORD_BYTES,
    MAX_SUBMATCHES,
    RawMatch,
    RawSubmatch,
)
from app.filesystem.search import Entry, GrepInput, GlobInput, Match, _sort_by_mtime
from app.filesystem.watcher import Event, WatchUpdate

mimetypes.init()


@pytest.fixture(autouse=True)
def _reset_rg():
    old = ripgrep._rg_path
    ripgrep._rg_path = None
    yield
    ripgrep._rg_path = old


def _mk(root: Path, rel: str, content: str = "needle here"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """替换 ripgrep.subprocess / sys.modules['subprocess'] 的 run()。

    必须暴露 TimeoutExpired/OSError 类，因为被测函数用
    `except (subprocess.TimeoutExpired, OSError)` 捕获异常。
    """

    TimeoutExpired = subprocess.TimeoutExpired
    OSError = OSError

    def __init__(self, procs=None):
        self._procs = list(procs) if procs else [_FakeProc()]
        self.calls = []

    def run(self, cmd, **kw):
        self.calls.append(cmd)
        item = self._procs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _patch_std_subprocess(monkeypatch, procs):
    fake = _FakeSubprocess(procs)
    monkeypatch.setitem(sys.modules, "subprocess", fake)
    return fake


# ---------------------------------------------------------------------------
# ripgrep
# ---------------------------------------------------------------------------


class TestRipgrepBasics:
    def test_constants(self):
        assert MAX_RECORD_BYTES == 64 * 1024
        assert MAX_SUBMATCHES == 100

    def test_error_hierarchy(self):
        assert issubclass(InvalidPatternError, ValueError)
        assert issubclass(InternalRipgrepError, RuntimeError)

    def test_binary_cached_none(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ripgrep, "_rg_path", None)
        # [B13] 清空仓库内预置候选，模拟"系统与 bundled 均无 rg"
        monkeypatch.setattr(ripgrep, "_RG_CANDIDATES", [])
        monkeypatch.setattr(ripgrep.shutil, "which", lambda n: calls.append(n) or None)
        assert ripgrep.ripgrep_binary() is None
        assert ripgrep.ripgrep_binary() is None
        assert len(calls) == 1  # 第二次命中缓存

    def test_binary_cached_string(self, monkeypatch):
        monkeypatch.setattr(ripgrep, "_rg_path", "rg")
        assert ripgrep.ripgrep_binary() == "rg"
        monkeypatch.setattr(ripgrep, "_rg_path", "")
        assert ripgrep.ripgrep_binary() is None

    def test_compile_invalid_pattern(self):
        with pytest.raises(InvalidPatternError):
            ripgrep._compile_pattern("[")


class TestRipgrepPythonFind:
    def test_python_find_matches(self, tmp_path):
        _mk(tmp_path, "a.txt", "foo needle bar\nnext line needle2")
        out = []
        ripgrep._python_find(FindInput(cwd=str(tmp_path), pattern="needle", on_entry=out.append))
        assert len(out) == 2
        m = out[0]
        assert m.line == 1
        assert m.column == 5
        assert m.filename == "a.txt"
        assert m.submatch.content == "needle"

    def test_python_find_limit(self, tmp_path):
        _mk(tmp_path, "a.txt", "\n".join("needle x%d" % i for i in range(50)))
        out = []
        ripgrep._python_find(FindInput(cwd=str(tmp_path), pattern="needle", limit=3, on_entry=out.append))
        assert len(out) == 3

    def test_python_find_skips_big_file(self, tmp_path):
        _mk(tmp_path, "big.txt", "x" * (MAX_RECORD_BYTES + 1) + " needle")
        _mk(tmp_path, "ok.txt", "needle")
        out = []
        ripgrep._python_find(FindInput(cwd=str(tmp_path), pattern="needle", on_entry=out.append))
        assert [m.filename for m in out] == ["ok.txt"]

    def test_python_find_invalid_pattern(self, tmp_path):
        with pytest.raises(InvalidPatternError):
            ripgrep._python_find(FindInput(cwd=str(tmp_path), pattern="["))


class TestRipgrepFind:
    def test_find_no_binary_python(self, tmp_path):
        _mk(tmp_path, "x.txt", "needle")
        results = ripgrep.find(FindInput(cwd=str(tmp_path), pattern="needle"))
        assert len(results) == 1
        assert isinstance(results[0], RawMatch)

    def test_find_on_entry_routed(self, tmp_path):
        _mk(tmp_path, "x.txt", "needle")
        seen = []
        ripgrep.find(FindInput(cwd=str(tmp_path), pattern="needle", on_entry=seen.append))
        assert len(seen) == 1

    def test_find_binary_success(self, tmp_path, monkeypatch):
        _mk(tmp_path, "x.txt", "needle")
        monkeypatch.setattr(ripgrep, "_rg_path", "fake-rg")
        monkeypatch.setattr(
            ripgrep,
            "_rg_find",
            lambda b, inp, on_match: on_match(RawMatch(
                filename="x.txt", line=1, column=1,
                submatch=RawSubmatch("needle", 0, 6),
            )),
        )
        results = ripgrep.find(FindInput(cwd=str(tmp_path), pattern="needle"))
        assert len(results) == 1 and results[0].filename == "x.txt"

    def test_find_binary_error_falls_back(self, tmp_path, monkeypatch):
        _mk(tmp_path, "x.txt", "needle")
        monkeypatch.setattr(ripgrep, "_rg_path", "fake-rg")

        def boom(binary, inp, on_match):
            raise InternalRipgrepError("boom")

        monkeypatch.setattr(ripgrep, "_rg_find", boom)
        results = ripgrep.find(FindInput(cwd=str(tmp_path), pattern="needle"))
        assert len(results) == 1  # 已降级纯 Python

    def test_find_invalid_pattern_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ripgrep, "_rg_path", "fake-rg")
        monkeypatch.setattr(ripgrep, "_rg_find", lambda b, i, m: (_ for _ in ()).throw(InvalidPatternError("bad")))
        with pytest.raises(InvalidPatternError):
            ripgrep.find(FindInput(cwd=str(tmp_path), pattern="["))


class TestRipgrepRgFind:
    def test_parse_json_matches(self, monkeypatch):
        lines = (
            '{"type":"begin"}\n'
            '{"type":"match","data":{"path":{"text":"a.txt"},"line_number":3,"absolute_offset":10,'
            '"submatches":[{"match":{"text":"needle"},"start":2,"end":8}]}}\n'
            '{"type":"end"}\n'
        )
        fake = _FakeSubprocess([_FakeProc(0, lines)])
        monkeypatch.setattr(ripgrep, "subprocess", fake)
        out = []
        ripgrep._rg_find("rg", FindInput(cwd=".", pattern="x"), out.append)
        assert len(out) == 1
        m = out[0]
        assert m.filename == "a.txt" and m.line == 3 and m.column == 10
        assert m.submatch.content == "needle" and m.submatch.start == 2 and m.submatch.end == 8

    def test_skips_non_match_and_bad_json(self, monkeypatch):
        lines = (
            '{"type":"match","data":{"path":{"text":"drop.txt"},"line_number":1,'  # 无 submatches
            '"absolute_offset":0,"submatches":[]}}\n'
            "not json here\n"
            '{"type":"match","data":{"path":{"text":"ok.txt"},"line_number":7,"absolute_offset":5,'
            '"submatches":[{"match":{"text":"x"},"start":0,"end":1}]}}\n'
        )
        fake = _FakeSubprocess([_FakeProc(0, lines)])
        monkeypatch.setattr(ripgrep, "subprocess", fake)
        out = []
        ripgrep._rg_find("rg", FindInput(cwd=".", pattern="x"), out.append)
        assert [m.filename for m in out] == ["ok.txt"]

    def test_limit(self, monkeypatch):
        lines = "".join(
            '{"type":"match","data":{"path":{"text":"f%d.txt"},"line_number":1,"absolute_offset":1,'
            '"submatches":[{"match":{"text":"x"},"start":0,"end":1}]}}\n' % i
            for i in range(10)
        )
        fake = _FakeSubprocess([_FakeProc(0, lines)])
        monkeypatch.setattr(ripgrep, "subprocess", fake)
        out = []
        ripgrep._rg_find("rg", FindInput(cwd=".", pattern="x", limit=3), out.append)
        assert len(out) == 3

    def test_returncode_2_invalid_pattern(self, monkeypatch):
        fake = _FakeSubprocess([_FakeProc(2, "", "bad regex")])
        monkeypatch.setattr(ripgrep, "subprocess", fake)
        with pytest.raises(InvalidPatternError):
            ripgrep._rg_find("rg", FindInput(cwd=".", pattern="x"), lambda m: None)

    def test_returncode_other_internal_error(self, monkeypatch):
        fake = _FakeSubprocess([_FakeProc(9, "", "boom")])
        monkeypatch.setattr(ripgrep, "subprocess", fake)
        with pytest.raises(InternalRipgrepError):
            ripgrep._rg_find("rg", FindInput(cwd=".", pattern="x"), lambda m: None)

    def test_timeout_or_oserror_internal_error(self, monkeypatch):
        fake = _FakeSubprocess([subprocess.TimeoutExpired("rg", 60)])
        monkeypatch.setattr(ripgrep, "subprocess", fake)
        with pytest.raises(InternalRipgrepError):
            ripgrep._rg_find("rg", FindInput(cwd=".", pattern="x"), lambda m: None)


# ---------------------------------------------------------------------------
# filesystem/search.py（glob/grep/find，纯 Python 回退为主）
# ---------------------------------------------------------------------------


class TestSearchGlobPython:
    def test_python_glob_dirs_and_files(self, tmp_path):
        _mk(tmp_path, "baz.txt", "x")
        _mk(tmp_path, "bar/inner.txt", "x")
        _mk(tmp_path, "skip.zip", "x")
        entries = search_mod._python_glob(GlobInput(pattern="b*", path=str(tmp_path)))
        assert {e.type for e in entries} == {"dir", "file"}
        assert {Path(e.path).name for e in entries} == {"bar", "baz.txt"}

    def test_python_glob_limit(self, tmp_path):
        for i in range(5):
            _mk(tmp_path, "f%d.txt" % i, "x")
        entries = search_mod._python_glob(GlobInput(pattern="*.txt", path=str(tmp_path), limit=2))
        assert len(entries) == 2

    def test_python_glob_respects_gitignore(self, tmp_path):
        _mk(tmp_path, ".gitignore", "ignored.txt\n")
        _mk(tmp_path, "ignored.txt", "x")
        _mk(tmp_path, "kept.txt", "x")
        entries = search_mod._python_glob(GlobInput(pattern="*.txt", path=str(tmp_path)))
        assert [Path(e.path).name for e in entries] == ["kept.txt"]

    def test_glob_no_binary_python(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.md", "x")
        monkeypatch.setattr(search_mod, "ripgrep_binary", lambda: None)
        entries = search_mod.glob(GlobInput(pattern="*.md", path=str(tmp_path)))
        assert len(entries) == 1

    def test_glob_rg_raises_python_fallback(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.md", "x")
        monkeypatch.setattr(search_mod, "ripgrep_binary", lambda: "rg")
        monkeypatch.setattr(search_mod, "_rg_glob", lambda b, i: (_ for _ in ()).throw(RuntimeError("boom")))
        entries = search_mod.glob(GlobInput(pattern="*.md", path=str(tmp_path)))
        assert len(entries) == 1


class TestSearchRgGlob:
    def test_parse_stdout(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.txt", "x")
        _mk(tmp_path, "dir", "__placeholder__")
        fake = _patch_std_subprocess(monkeypatch, [_FakeProc(0, "a.txt\ndir\n")])
        entries = search_mod._rg_glob("rg", GlobInput(pattern="*", path=str(tmp_path)))
        assert fake.calls[0] == ["rg", "--files", "--glob", "*", "."]
        names = {Path(e.path).name for e in entries}
        assert names == {"a.txt", "dir"}

    def test_returncode_1_empty(self, tmp_path, monkeypatch):
        _patch_std_subprocess(monkeypatch, [_FakeProc(1, "")])
        assert search_mod._rg_glob("rg", GlobInput(pattern="*", path=str(tmp_path))) == []

    def test_returncode_2_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(search_mod, "_python_glob", lambda inp: ["FALLBACK"])
        _patch_std_subprocess(monkeypatch, [_FakeProc(2)])
        assert search_mod._rg_glob("rg", GlobInput(pattern="*", path=str(tmp_path))) == ["FALLBACK"]

    def test_oserror_fallback(self, tmp_path, monkeypatch):
        sentinel = ["FALLBACK"]
        monkeypatch.setattr(search_mod, "_python_glob", lambda inp: sentinel)
        _patch_std_subprocess(monkeypatch, [OSError("no rg")])
        assert search_mod._rg_glob("rg", GlobInput(pattern="*", path=str(tmp_path))) == sentinel


class TestSearchGrepPython:
    def test_grep_regex_matches(self, tmp_path):
        _mk(tmp_path, "a.txt", "hello world\nnothing")
        _mk(tmp_path, "b.txt", "no hit")
        hits = search_mod._python_grep(GrepInput(pattern="world", path=str(tmp_path)))
        assert len(hits) == 1
        assert hits[0].path == str((tmp_path / "a.txt").resolve())
        assert hits[0].line == 1
        assert hits[0].column == 7
        assert hits[0].text == "hello world"

    def test_grep_multiple_per_line(self, tmp_path):
        _mk(tmp_path, "a.txt", "aa aa")
        hits = search_mod._python_grep(GrepInput(pattern="a", path=str(tmp_path)))
        assert len(hits) == 4

    def test_grep_invalid_pattern_substring(self, tmp_path):
        _mk(tmp_path, "a.txt", "a[bc] needle")
        _mk(tmp_path, "b.txt", "other")
        hits = search_mod._python_grep(GrepInput(pattern="a[", path=str(tmp_path)))
        assert len(hits) == 1 and hits[0].column == 1

    def test_grep_include_filter(self, tmp_path):
        _mk(tmp_path, "a.py", "needle word")
        _mk(tmp_path, "b.txt", "needle word")
        hits = search_mod._python_grep(GrepInput(pattern="needle", path=str(tmp_path), include="*.py"))
        assert [Path(h.path).name for h in hits] == ["a.py"]

    def test_grep_skip_big_file(self, tmp_path):
        _mk(tmp_path, "big.txt", "x" * (MAX_RECORD_BYTES + 1) + " needle")
        _mk(tmp_path, "small.txt", "needle")
        hits = search_mod._python_grep(GrepInput(pattern="needle", path=str(tmp_path)))
        assert [Path(h.path).name for h in hits] == ["small.txt"]

    def test_grep_limit(self, tmp_path):
        _mk(tmp_path, "a.txt", "\n".join("needle %d" % i for i in range(30)))
        hits = search_mod._python_grep(GrepInput(pattern="needle", path=str(tmp_path), limit=5))
        assert len(hits) == 5

    def test_grep_no_binary_python(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.txt", "needle")
        monkeypatch.setattr(search_mod, "ripgrep_binary", lambda: None)
        hits = search_mod.grep(GrepInput(pattern="needle", path=str(tmp_path)))
        assert len(hits) == 1

    def test_grep_rg_raises_python_fallback(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.txt", "needle")
        monkeypatch.setattr(search_mod, "ripgrep_binary", lambda: "rg")
        monkeypatch.setattr(search_mod, "_rg_grep", lambda b, i: (_ for _ in ()).throw(RuntimeError("boom")))
        hits = search_mod.grep(GrepInput(pattern="needle", path=str(tmp_path)))
        assert len(hits) == 1


class TestSearchRgGrep:
    def test_parse_lines(self, tmp_path, monkeypatch):
        _mk(tmp_path, "a.txt", "x")
        fake = _patch_std_subprocess(monkeypatch, [_FakeProc(0, "sub/a.txt:5:1:hello:world\n")])
        hits = search_mod._rg_grep("rg", GrepInput(pattern="hello", path=str(tmp_path)))
        assert fake.calls[0][:5] == ["rg", "--no-heading", "--line-number", "--column", "hello"]
        assert len(hits) == 1
        assert hits[0].line == 5 and hits[0].column == 1
        assert hits[0].text == "hello:world"

    def test_include_adds_glob_flag(self, tmp_path, monkeypatch):
        fake = _patch_std_subprocess(monkeypatch, [_FakeProc(1)])
        search_mod._rg_grep("rg", GrepInput(pattern="x", path=str(tmp_path), include="*.py"))
        assert "--glob" in fake.calls[0]
        assert "*.py" in fake.calls[0]

    def test_returncode_2_invalid(self, tmp_path, monkeypatch):
        _patch_std_subprocess(monkeypatch, [_FakeProc(2)])
        assert search_mod._rg_grep("rg", GrepInput(pattern="x", path=str(tmp_path))) == []

    def test_returncode_other_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(search_mod, "_python_grep", lambda inp: ["FALLBACK"])
        _patch_std_subprocess(monkeypatch, [_FakeProc(7)])
        assert search_mod._rg_grep("rg", GrepInput(pattern="x", path=str(tmp_path))) == ["FALLBACK"]

    def test_timeout_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(search_mod, "_python_grep", lambda inp: ["FALLBACK"])
        _patch_std_subprocess(monkeypatch, [subprocess.TimeoutExpired("rg", 60)])
        assert search_mod._rg_grep("rg", GrepInput(pattern="x", path=str(tmp_path))) == ["FALLBACK"]

    def test_limit_and_malformed(self, tmp_path, monkeypatch):
        out = "\n".join("f%d.txt:1:1:x" % i for i in range(5)) + "\nmalformed-line-no-cols\n"
        _patch_std_subprocess(monkeypatch, [_FakeProc(0, out)])
        hits = search_mod._rg_grep("rg", GrepInput(pattern="x", path=str(tmp_path), limit=2))
        assert len(hits) == 2


class TestSearchFind:
    def test_find_files_basename(self, tmp_path):
        _mk(tmp_path, "a/b.md", "x")
        entries = search_mod.find_files("b.md", str(tmp_path))
        assert len(entries) == 1

    def test_find_files_relpath_and_limit(self, tmp_path):
        _mk(tmp_path, "a/b.md", "x")
        _mk(tmp_path, "c.md", "x")
        entries = search_mod.find_files("*.md", str(tmp_path), limit=1)
        assert len(entries) == 1

    def test_sort_by_mtime(self, tmp_path):
        old = _mk(tmp_path, "old.txt", "x")
        new = _mk(tmp_path, "new.txt", "x")
        os.utime(old, ns=(1_000_000_000, 1_000_000_000))
        os.utime(new, ns=(2_000_000_000, 2_000_000_000))
        entries = [Entry(str(new)), Entry(str(old)), Entry(str(tmp_path / "gone.txt"))]
        ordered = _sort_by_mtime(entries)
        assert [Path(e.path).name for e in ordered] == ["new.txt", "old.txt", "gone.txt"]

    def test_glob_input_dataclasses(self):
        assert GlobInput(pattern="*", path=".").limit == 100
        assert GrepInput(pattern="x", path=".").include == ""
        m = Match(path="p", line=1, column=1, text="t")
        assert m.text == "t"


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------


class TestShell:
    def test_name(self):
        assert shell_mod.name(r"C:\x\powershell.exe") == "powershell"
        assert shell_mod.name("BASH") == "bash"
        assert shell_mod.name("pwsh.exe") == "pwsh"

    def test_ps(self):
        assert shell_mod.ps("powershell")
        assert shell_mod.ps("pwsh.exe")
        assert not shell_mod.ps("bash")

    def test_posix(self):
        assert shell_mod.posix("bash")
        assert shell_mod.posix("sh")
        assert shell_mod.posix("zsh.exe")
        assert not shell_mod.posix("cmd")

    def test_acceptable(self):
        assert shell_mod.acceptable("cmd")
        assert shell_mod.acceptable("powershell")
        assert shell_mod.acceptable("bash")
        assert not shell_mod.acceptable("fish")

    def test_platform_shell_nt(self, monkeypatch):
        monkeypatch.setattr(shell_mod, "_powershell_available", lambda: True)
        assert shell_mod.platform_shell() in ("powershell", "bash")
        monkeypatch.setattr(shell_mod, "_powershell_available", lambda: False)
        assert shell_mod.platform_shell() in ("cmd", "bash")

    def test_powershell_available(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda n: "C:\\x\\powershell.exe")
        assert shell_mod._powershell_available() is True
        monkeypatch.setattr("shutil.which", lambda n: (_ for _ in ()).throw(OSError("boom")))
        assert shell_mod._powershell_available() is False

    def test_shell_args(self):
        assert shell_mod.shell_args("powershell.exe", "echo 1") == [
            "powershell.exe", "-NoProfile", "-Command", "echo 1",
        ]
        assert shell_mod.shell_args("bash", "ls") == ["bash", "-c", "ls"]
        assert shell_mod.shell_args("cmd.exe", "dir") == ["cmd.exe", "/c", "dir"]

    def test_shell_args_unsupported(self):
        with pytest.raises(ValueError):
            shell_mod.shell_args("fish", "x")


# ---------------------------------------------------------------------------
# watcher
# ---------------------------------------------------------------------------


class TestWatcher:
    def test_has_native_binding_false(self):
        assert watcher_mod.has_native_binding() is False

    def test_event_enum(self):
        assert Event.Updated.value == "updated"
        assert Event.Edited.value == "edited"

    def test_watch_update_defaults(self):
        ev = WatchUpdate(type=Event.Updated, path="p")
        assert ev.operation == "update"

    def test_ctor_resolves_root_and_clamps_interval(self, tmp_path):
        w = watcher_mod.FileSystemWatcher(str(tmp_path / "sub"), interval_ms=50)
        assert w.root == (tmp_path / "sub").resolve()
        assert w.interval == 200

    def test_poll_create_events(self, tmp_path):
        d = tmp_path / "w"
        d.mkdir()
        seen = []
        w = watcher_mod.FileSystemWatcher(str(d))
        w.subscribe(seen.append)
        _mk(d, "sub/a.txt", "x")
        events = w.poll()
        assert {Path(e.path).name for e in events} == {"sub", "a.txt"}
        assert all(e.operation == "create" for e in events)
        assert len(seen) == len(events)

    def test_poll_second_no_events(self, tmp_path):
        d = tmp_path / "w"
        d.mkdir()
        _mk(d, "a.txt", "x")
        w = watcher_mod.FileSystemWatcher(str(d))
        assert w.poll()
        assert w.poll() == []

    def test_poll_detect_update_and_delete(self, tmp_path):
        d = tmp_path / "w"
        d.mkdir()
        f = _mk(d, "a.txt", "abc")
        w = watcher_mod.FileSystemWatcher(str(d))
        w.poll()
        f.write_text("abcd", encoding="utf-8")
        updates = w.poll()
        assert any(e.operation == "update" and Path(e.path).name == "a.txt" for e in updates)
        f.unlink()
        deletes = w.poll()
        assert any(e.operation == "delete" for e in deletes)

    def test_callback_exception_swallowed(self, tmp_path):
        d = tmp_path / "w"
        d.mkdir()

        def boom(ev):
            raise RuntimeError("nope")

        w = watcher_mod.FileSystemWatcher(str(d))
        w.subscribe(boom)
        w.subscribe(lambda ev: seen.append(ev))
        seen = []
        _mk(d, "a.txt", "x")
        events = w.poll()
        assert len(events) == 1 and seen == events

    def test_start_close(self, tmp_path):
        d = tmp_path / "w"
        d.mkdir()

        def cb(ev):
            pass

        w = watcher_mod.FileSystemWatcher(str(d), interval_ms=50)
        w.subscribe(cb)
        w.start()
        try:
            assert hasattr(w, "_thread") and w._thread.daemon
            time.sleep(0.05)
        finally:
            w.close()
        assert w._closed is True


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


class TestProject:
    def test_project_dataclass(self, tmp_path):
        p = project_mod.Project(worktree=str(tmp_path), id="pid")
        assert p.root == tmp_path
        assert p.is_inside(str(tmp_path / "a" / "b.txt"))
        assert not p.is_inside(str(tmp_path.parent / "elsewhere"))
        assert p.relative(str(tmp_path / "a" / "b.txt")) == "a/b.txt"

    def test_from_directory_with_git(self, tmp_path):
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        proj = project_mod.Project.from_directory(str(root))
        assert Path(proj.worktree) == root.resolve()
        assert proj.id.startswith(("path:", "git:"))

    def test_from_directory_no_git(self, tmp_path):
        proj = project_mod.Project.from_directory(str(tmp_path))
        assert proj.worktree == str(tmp_path.resolve())

    def test_from_git_success(self, tmp_path, monkeypatch):
        worktree = tmp_path / "wt"
        worktree.mkdir()

        def fake_run(cmd, **kw):
            return SimpleNamespace(returncode=0, stdout=str(worktree) + "\n", stderr="")

        monkeypatch.setattr(project_mod.subprocess, "run", fake_run)
        proj = project_mod.Project.from_git(str(tmp_path))
        assert proj.worktree == str(worktree.resolve())
        assert proj.id.startswith("git:")

    def test_from_git_fallback_on_failure(self, tmp_path, monkeypatch):
        marker = SimpleNamespace()

        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd[0], 5)

        monkeypatch.setattr(project_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(project_mod.Project, "from_directory", lambda cls, directory=None: marker)
        assert project_mod.Project.from_git(str(tmp_path)) is marker


class TestProjectId:
    def test_reads_persisted_json(self, tmp_path):
        meta = tmp_path / ".git" / "opencode" / "project.json"
        meta.parent.mkdir(parents=True)
        meta.write_text('{"id":"fixed-id","worktree":"x"}', encoding="utf-8")
        assert project_mod._project_id(str(tmp_path)) == "fixed-id"

    def test_git_hash_branch(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            return SimpleNamespace(returncode=0, stdout="a1b2c3d4e5f6g7\n", stderr="")

        monkeypatch.setattr(project_mod.subprocess, "run", fake_run)
        assert project_mod._project_id(str(tmp_path)) == "git:a1b2c3d4e5f6"

    def test_path_fallback_and_persist(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd[0], 5)

        monkeypatch.setattr(project_mod.subprocess, "run", fake_run)
        pid = project_mod._project_id(str(tmp_path))
        assert pid.startswith("path:")
        meta = tmp_path / ".git" / "opencode" / "project.json"
        assert meta.exists() and "path:" in meta.read_text(encoding="utf-8")

    def test_invalid_json_falls_to_fallback(self, tmp_path, monkeypatch):
        meta = tmp_path / ".git" / "opencode" / "project.json"
        meta.parent.mkdir(parents=True)
        meta.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(project_mod.subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd[0], 5)))
        pid = project_mod._project_id(str(tmp_path))
        assert pid.startswith("path:")

    def test_persist_failure_warns(self, tmp_path, monkeypatch, caplog):
        (tmp_path / ".git").write_text("not-a-dir", encoding="utf-8")  # .git 是文件
        monkeypatch.setattr(
            project_mod.subprocess,
            "run",
            lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd[0], 5)),
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="app.filesystem.project"):
            pid = project_mod._project_id(str(tmp_path))
        assert pid.startswith("path:")
        assert "cannot persist project id" in caplog.text


# ---------------------------------------------------------------------------
# FileSystem 服务抽象
# ---------------------------------------------------------------------------


class TestFileSystemService:
    @pytest.fixture
    def fsroot(self, tmp_path):
        root = tmp_path / "fs"
        _mk(root, "a.txt", "needle body")
        _mk(root, "sub/b.md", "other")
        return root

    def test_locate_absolute_and_relative(self, fsroot):
        fs = fssvc.FileSystem(root=str(fsroot))
        assert fs._locate("a.txt") == (fsroot / "a.txt").resolve()
        assert fs._locate(str(fsroot / "a.txt")) == (fsroot / "a.txt").resolve()

    def test_locate_no_root(self, fsroot, monkeypatch):
        fs = fssvc.FileSystem()
        got = fs._locate("some/nonexistent.txt")
        assert Path(got).is_absolute()

    def test_locate_injected_resolve(self, fsroot):
        fs = fssvc.FileSystem(resolve=lambda p: str(fsroot / "a.txt"))
        assert fs._locate("anything") == (fsroot / "a.txt")

    def test_read_bytes(self, fsroot):
        fs = fssvc.FileSystem(root=str(fsroot))
        assert fs.read("a.txt") == b"needle body"

    def test_list(self, fsroot):
        fs = fssvc.FileSystem(root=str(fsroot))
        names = [(e.name, e.type) for e in fs.list(".")]
        assert ("a.txt", "file") in names
        assert ("sub", "dir") in names

    def test_glob(self, fsroot, monkeypatch):
        monkeypatch.setattr(search_mod, "ripgrep_binary", lambda: None)
        fs = fssvc.FileSystem(root=str(fsroot))
        hits = fs.glob("*.txt", ".")
        assert [Path(e.path).name for e in hits] == ["a.txt"]

    def test_grep(self, fsroot, monkeypatch):
        monkeypatch.setattr(search_mod, "ripgrep_binary", lambda: None)
        fs = fssvc.FileSystem(root=str(fsroot))
        hits = fs.grep("needle", ".")
        assert len(hits) == 1
        assert hits[0].entry.path.endswith("a.txt")
        assert hits[0].line == 1

    def test_find(self, fsroot):
        fs = fssvc.FileSystem(root=str(fsroot))
        hits = fs.find("*.md", ".")
        assert len(hits) == 1

    def test_entry_match_dataclasses(self):
        e = fssvc.Entry(path="x", type="dir")
        assert e.type == "dir"
        m = fssvc.Match(entry=e, line=1, column=2, text="t")
        assert m.column == 2

    def test_assert_location_inside(self, tmp_path):
        fssvc.assert_location(str(tmp_path / "a"), str(tmp_path))

    def test_assert_location_outside(self, tmp_path):
        with pytest.raises(fssvc.LocationResolvingError):
            fssvc.assert_location(str(tmp_path.parent / "other"), str(tmp_path))

    def test_assert_location_custom_message(self, tmp_path):
        with pytest.raises(fssvc.LocationResolvingError, match="custom"):
            fssvc.assert_location("x", "y", "custom msg")


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class TestModels:
    def test_file_status_values(self):
        assert fsmodels.FileStatus.MODIFIED.value == "modified"
        assert list(fsmodels.FileStatus) == [
            fsmodels.FileStatus.ADDED,
            fsmodels.FileStatus.MODIFIED,
            fsmodels.FileStatus.DELETED,
            fsmodels.FileStatus.UNTRACKED,
            fsmodels.FileStatus.UNCHANGED,
        ]

    def test_file_info_defaults(self):
        info = fsmodels.FileInfo(path="a.txt")
        assert info.added == 0 and info.removed == 0
        assert info.status == fsmodels.FileStatus.UNCHANGED

    def test_file_node_properties(self, tmp_path):
        d = fsmodels.FileNode(name="d", path="d", absolute=str(tmp_path), type="dir")
        f = fsmodels.FileNode(name="f", path="f", absolute=str(tmp_path / "f"), type="file", ignored=True)
        assert d.is_dir and not d.is_file
        assert f.is_file and not f.is_dir
        assert f.ignored is True

    def test_file_content_utf8(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("hello", encoding="utf-8")
        fc = fsmodels.FileContent.from_path(str(p))
        assert fc.content == "hello"
        assert fc.encoding == "utf-8"
        assert fc.mime_type == "text/plain"
        assert fc.writeable is True

    def test_file_content_utf16(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_bytes("你好".encode("utf-16"))
        fc = fsmodels.FileContent.from_path(str(p))
        assert fc.content == "你好"
        assert fc.encoding == "utf-16"
        assert fc.writeable is True

    def test_file_content_binary(self, tmp_path):
        p = tmp_path / "a.bin"
        p.write_bytes(b"\xff\xff\xff")
        fc = fsmodels.FileContent.from_path(str(p))
        assert fc.encoding == "binary"
        assert fc.content == ""

    def test_file_content_missing_treated_writable(self, tmp_path):
        p = tmp_path / "new.txt"
        fc = fsmodels.FileContent.from_path(str(p))
        assert fc.content == "" and fc.encoding == "utf-8"
        assert fc.writeable is True
        assert fc.mime_type == "text/plain"

    def test_file_content_base(self, tmp_path):
        p = tmp_path / "a" / "b.txt"
        p.parent.mkdir(parents=True)
        p.write_text("x", encoding="utf-8")
        fc = fsmodels.FileContent.from_path(str(p), base=str(tmp_path))
        assert fc.path == "a/b.txt"

    def test_file_content_dir_not_writable(self, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        fc = fsmodels.FileContent.from_path(str(d))
        assert fc.writeable is False

    def test_is_writeable_missing_true(self, tmp_path):
        assert fsmodels._is_writeable(tmp_path / "gone") is True
        d = tmp_path / "adir"
        d.mkdir()
        assert fsmodels._is_writeable(d) is False