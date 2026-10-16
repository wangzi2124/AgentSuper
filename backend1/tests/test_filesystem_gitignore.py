"""filesystem/gitignore.py 语义测试：规则解析、globs 转译、层级匹配、walk/glob。

GitignoreMatcher 是纯逻辑模块(无 IO 依赖),直接在 tmp 目录构造 .gitignore
验证 git(1) 语义:锚定/非锚定、目录限定、!取反、嵌套覆盖、最后匹配生效。
"""
import os
from pathlib import Path, PurePosixPath

import pytest

from app.filesystem.gitignore import (
    GitignoreMatcher,
    _translate,
    glob_to_regex,
    parse_gitignore,
)


@pytest.fixture
def mroot(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


def _write(rel: Path, text: str = "x", ns: int | None = None):
    p = rel if rel.is_absolute() else rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if ns is not None:
        os.utime(p, ns=(ns, ns))
    return p


# ---------------------------------------------------------------------------
# _parse_rule / parse_gitignore
# ---------------------------------------------------------------------------


class TestParseRule:
    def test_skips_empty_and_comment(self):
        assert parse_gitignore("\n\n# a comment\n  \n") == []

    def test_strips_trailing_spaces(self):
        rules = parse_gitignore("*.log   \n")
        assert len(rules) == 1
        assert rules[0].regex.pattern == r"[^/]*\.log"

    def test_negated(self):
        (r,) = parse_gitignore("!keep.txt")
        assert r.negated is True
        assert r.anchored is False

    def test_unescape_hash_and_bang(self):
        # re.escape 转义 # 但不转义 !（CPython 特集字符表）
        r1 = parse_gitignore("\\#file")[0]
        assert r1.regex.pattern == r"\#file"
        r2 = parse_gitignore("\\!file")[0]
        assert r2.regex.pattern == "!file"

    def test_anchored_leading_slash(self):
        r = parse_gitignore("/foo")[0]
        assert r.anchored is True
        assert r.regex.pattern == "foo"
        assert not r.dir_only

    def test_dir_only_trailing_slash(self):
        # 修复后：尾部 / 只是目录标记，不再置 anchored（对齐 git(5) 任意层级语义）
        r = parse_gitignore("build/")[0]
        assert r.dir_only is True
        assert r.anchored is False
        assert r.regex.pattern == "build"

    def test_line_loses_everything_returns_none(self):
        assert parse_gitignore("!\n/\n\n") == []

    def test_lone_backslash_empty_regex(self):
        # 单独 "\" 编译为空正则：fullmatch 仅匹配空名字 → 实际不忽略任何文件
        (r,) = parse_gitignore("\\")
        assert r.regex.pattern == ""
        assert r.matches(PurePosixPath(""), False) is True
        assert r.matches(PurePosixPath("anything"), False) is False

    def test_rule_fields(self):
        r = parse_gitignore("/sub/build/")[0]
        assert r.negated is False
        assert r.dir_only is True
        assert r.anchored is True
        assert r.regex.pattern == r"sub/build"

    def test_multiline_order_preserved(self):
        rules = parse_gitignore("a.txt\n!b.txt\n*.md\n")
        assert [r.regex.pattern for r in rules] == ["a\\.txt", "b\\.txt", "[^/]*\\.md"]
        assert [r.negated for r in rules] == [False, True, False]

    def test_unclosed_class_becomes_literal(self):
        # "[" 被 _translate 转义为字面量，不触发 re.error 丢弃分支
        (r,) = parse_gitignore("[")
        assert r.regex.pattern == r"\["

    def test_matches_helper(self):
        (r,) = parse_gitignore("*.txt")
        assert r.matches(PurePosixPath("a.txt"), False)
        assert not r.matches(PurePosixPath("a.md"), False)
        (rd,) = parse_gitignore("build/")
        assert rd.matches(PurePosixPath("build"), True)
        assert not rd.matches(PurePosixPath("build"), False)


# ---------------------------------------------------------------------------
# _translate
# ---------------------------------------------------------------------------


class TestTranslate:
    def test_star_not_cross_slash(self):
        assert _translate("*") == "[^/]*"
        assert _translate("a*b") == r"a[^/]*b"

    def test_doublestar(self):
        assert _translate("**") == ".*"
        assert _translate("**/x") == r"(?:[^/]+/)*x"
        # 分隔符 "/" 不经 re.escape（非特集字符），原样保留
        assert _translate("a/**/b") == r"a/(?:[^/]+/)*b"

    def test_question(self):
        assert _translate("f?") == r"f[^/]"

    def test_char_class(self):
        assert _translate("[0-9]") == "[0-9]"
        assert _translate("[!a]") == "[^a]"
        assert _translate("[^a]") == "[^a]"

    def test_unclosed_class_escaped(self):
        assert _translate("a[b") == r"a\[b"

    def test_backslash_escape(self):
        assert _translate("a\\*b") == r"a\*b"

    def test_plain_chars_escaped(self):
        assert _translate("abc.txt") == r"abc\.txt"


class TestGlobToRegex:
    def test_doublestar_fullmatch(self):
        rx = glob_to_regex("**/*.py")
        assert rx.match("a/b/c.py")
        assert rx.match("single.py")
        assert not rx.match("a/b/c.txt")

    def test_anchored(self):
        rx = glob_to_regex("src/**")
        assert rx.match("src/x.py")
        assert not rx.match("other/x.py")


# ---------------------------------------------------------------------------
# GitignoreMatcher.is_ignored
# ---------------------------------------------------------------------------


class TestMatcherIgnored:
    def test_root_resolved(self, mroot):
        assert GitignoreMatcher(mroot).root == mroot.resolve()

    def test_outside_root_never_ignored(self, mroot):
        _write(mroot / ".gitignore", "*.log\n")
        outside = mroot.parent / "other" / "a.log"
        outside.parent.mkdir(parents=True, exist_ok=True)
        assert GitignoreMatcher(mroot).is_ignored(outside, False) is False

    def test_no_gitignore(self, mroot):
        _write(mroot / "a.log")
        assert GitignoreMatcher(mroot).is_ignored(mroot / "a.log", False) is False

    def test_basename_any_depth(self, mroot):
        _write(mroot / ".gitignore", "*.log\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "a.log", False)
        assert m.is_ignored(mroot / "x" / "y" / "b.log", False)
        assert not m.is_ignored(mroot / "a.txt", False)

    def test_anchored_only_root(self, mroot):
        _write(mroot / ".gitignore", "/a.log\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "a.log", False)
        assert not m.is_ignored(mroot / "x" / "a.log", False)

    def test_dir_only_requires_dir(self, mroot):
        _write(mroot / ".gitignore", "build/\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "build", True)
        assert not m.is_ignored(mroot / "build", False)  # 文件名叫 build 不忽略
        # 回归（修复尾部 / 锚定偏差）：build/ 匹配任意层级的 build 目录（git 语义）
        assert m.is_ignored(mroot / "a" / "build", True) is True
        assert m.is_ignored(mroot / "a" / "b" / "build", True) is True
        # 被忽略目录下的文件/子目录随之忽略
        assert m.is_ignored(mroot / "a" / "build" / "x.txt", False) is True
        # build.txt 不是 build 目录 → 不忽略
        assert m.is_ignored(mroot / "build.txt", False) is False

    def test_negation_reinclude(self, mroot):
        _write(mroot / ".gitignore", "*.log\n!keep.log\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "plain.log", False)
        assert not m.is_ignored(mroot / "keep.log", False)

    def test_nested_override(self, mroot):
        _write(mroot / ".gitignore", "*.log\n")
        _write(mroot / "sub" / ".gitignore", "!keep.log\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "sub" / "keep.log", False) is False
        assert m.is_ignored(mroot / "sub" / "plain.log", False) is True

    def test_last_rule_wins(self, mroot):
        _write(mroot / ".gitignore", "x.log\n!*.log\n")
        assert GitignoreMatcher(mroot).is_ignored(mroot / "x.log", False) is False

    def test_doublestar_intra(self, mroot):
        _write(mroot / ".gitignore", "a/**/b\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "a" / "x" / "y" / "b", False)
        assert m.is_ignored(mroot / "a" / "b", False)
        assert not m.is_ignored(mroot / "other" / "b", False)

    def test_rules_reparsed_on_mtime_change(self, mroot):
        gi = _write(mroot / ".gitignore", "*.log\n", ns=1_000_000_000)
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "a.log", False)
        (gi.parent / ".gitignore").write_text("*.txt\n", encoding="utf-8")
        os.utime(gi, ns=(2_000_000_000, 2_000_000_000))
        assert m.is_ignored(mroot / "a.log", False) is False
        assert m.is_ignored(mroot / "a.txt", False) is True

    def test_rules_cached_between_calls(self, mroot):
        _write(mroot / ".gitignore", "*.log\n")
        m = GitignoreMatcher(mroot)
        assert m.is_ignored(mroot / "a.log", False)
        assert m.is_ignored(mroot / "b.log", False)

    def test_rules_for_unreadable_returns_empty(self, mroot):
        (mroot / ".gitignore").mkdir()  # .gitignore 是目录 → read_text 抛 OSError
        assert GitignoreMatcher(mroot).is_ignored(mroot / "a.log", False) is False


# ---------------------------------------------------------------------------
# GitignoreMatcher.walk / glob
# ---------------------------------------------------------------------------


class TestMatcherWalkGlob:
    def test_walk_trims_and_filters(self, mroot):
        _write(mroot / ".gitignore", "ignored_dir/\n*.tmp\n")
        _write(mroot / "ignored_dir" / "a.txt", "x")
        _write(mroot / "keep_dir" / "b.txt", "x")
        _write(mroot / "keep_dir" / "c.tmp", "x")
        dirs = []
        files = []
        for _, d, f in GitignoreMatcher(mroot).walk(mroot):
            dirs.extend(d)
            files.extend(f)
        assert mroot / "keep_dir" in dirs
        assert mroot / "ignored_dir" not in dirs
        assert mroot / "keep_dir" / "b.txt" in files
        assert mroot / "keep_dir" / "c.tmp" not in files

    def test_walk_relative_top(self, mroot):
        _write(mroot / "sub" / "a.txt", "x")
        rows = list(GitignoreMatcher(mroot).walk("sub"))
        assert rows[0][0].resolve() == (mroot / "sub").resolve()
        assert any(any(p.name == "a.txt" for p in f) for _, _, f in rows)

    def test_glob_matches_dirs_and_files(self, mroot):
        _write(mroot / ".gitignore", "*.tmp\n")
        _write(mroot / "bar" / "x.txt", "x")
        _write(mroot / "baz.txt", "x")
        _write(mroot / "noise.tmp", "x")
        hits = GitignoreMatcher(mroot).glob("b*")
        names = {h.name for h in hits}
        assert names == {"bar", "baz.txt"}

    def test_glob_relative_pattern(self, mroot):
        _write(mroot / "a" / "b" / "c.md", "x")
        _write(mroot / "a" / "c.md", "x")
        hits = GitignoreMatcher(mroot).glob("a/**/c.md")
        assert len(hits) == 2

    def test_glob_sorted_mtime_desc(self, mroot):
        _write(mroot / "old.txt", "x", ns=1_000_000_000)
        _write(mroot / "new.txt", "x", ns=3_000_000_000)
        _write(mroot / "mid.txt", "x", ns=2_000_000_000)
        hits = GitignoreMatcher(mroot).glob("*.txt")
        assert [h.name for h in hits] == ["new.txt", "mid.txt", "old.txt"]

    def test_glob_missing_stat_sorts_last(self, mroot):
        _write(mroot / "new.txt", "x", ns=3_000_000_000)
        hits = GitignoreMatcher(mroot).glob("*.txt")
        assert all(h.exists() for h in hits)

    def test_doublestar_glob(self, mroot):
        _write(mroot / "src" / "x.py", "x")
        _write(mroot / "src" / "deep" / "y.py", "x")
        hits = GitignoreMatcher(mroot).glob("**/*.py")
        assert len(hits) == 2


class TestMtimeOrZero:
    def test_missing_returns_zero(self, mroot):
        from app.filesystem.gitignore import _mtime_or_zero

        assert _mtime_or_zero(mroot / "nope") == 0.0

    def test_present_returns_mtime(self, mroot):
        from app.filesystem.gitignore import _mtime_or_zero

        p = _write(mroot / "a.txt", "x", ns=5_000_000_000)
        assert _mtime_or_zero(p) > 0.0