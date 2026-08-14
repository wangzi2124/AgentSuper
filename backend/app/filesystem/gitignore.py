"""
gitignore 匹配器(自包含,无第三方依赖)。

opencode 的 grep/glob 底层用 ripgrep,默认尊重 .gitignore 与 .ignore。
AgentSuper 用纯 pathlib 手写遍历,因此本模块提供等价的 gitignore 判定,
供 ScanCache、tool_glob、tool_grep 过滤被忽略路径。

语义对齐 git(gitignore(5)):
  - 每行一个模式;空白行与 # 开头的注释行被跳过
  - 行尾(非转义)空格被去除;模式中的 \\# 与 \\! 转义得到字面字符
  - 尾部 / 表示该模式仅匹配目录
  - 前导 ! 表示取反(重新包含),最后一条匹配的模式生效
  - 模式中包含 /(含前导 /)时锚定到 .gitignore 所在目录;
    否则匹配任意层级的 basename
  - ** 跨目录;* 不跨 /;? 匹配单个非 / 字符;[...] 字符类
  - 嵌套 .gitignore 优先级高于父级(层级顺序求值)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional

_IGNORE_PATTERN_RE = re.compile(r"(?<!\\)\s+$")


@dataclass
class _Rule:
    """一条解析后的 .gitignore 规则。"""

    negated: bool        # ! 取反
    dir_only: bool       # 尾部 / 仅目录
    anchored: bool       # 包含 / → 锚定到所在目录
    regex: "re.Pattern[str]"

    def matches(self, rel: PurePosixPath, is_dir: bool) -> bool:
        """判断相对路径是否匹配本规则。

        rel 是相对规则所在目录的 POSIX 路径。锚定模式匹配整个相对路径;
        非锚定模式只匹配 basename(git 的任意层级语义)。
        """
        if self.dir_only and not is_dir:
            return False
        if self.anchored:
            return bool(self.regex.fullmatch(str(rel)))
        return bool(self.regex.fullmatch(rel.name))


def _translate(pattern: str) -> str:
    """将 gitignore glob 片段翻译为正则表达式片段。"""
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:[^/]+/)*")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in ("!", "^"):
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j < n:
                cls = pattern[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls.replace("\\", "\\\\") + "]")
                i = j + 1
            else:
                out.append(re.escape(c))
                i += 1
        elif c == "\\":
            if i + 1 < n:
                out.append(re.escape(pattern[i + 1]))
                i += 2
            else:
                i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _parse_rule(line: str) -> Optional[_Rule]:
    """解析单行 .gitignore 规则;空行/注释返回 None。"""
    if not line:
        return None
    line = _IGNORE_PATTERN_RE.sub("", line)  # 去行尾非转义空格
    if not line or line.startswith("#"):
        return None
    negated = False
    if line.startswith("!"):
        negated = True
        line = line[1:]
    if line.startswith("\\#") or line.startswith("\\!"):
        line = line[1:]
    if not line:
        return None
    anchored = "/" in line
    if anchored and line.startswith("/"):
        line = line[1:]
    if not line:
        return None
    dir_only = False
    if line.endswith("/"):
        dir_only = True
        line = line[:-1]
    if not line:
        return None
    try:
        regex = re.compile(_translate(line))
    except re.error:
        return None
    return _Rule(negated=negated, dir_only=dir_only, anchored=anchored, regex=regex)


def parse_gitignore(text: str) -> list[_Rule]:
    """解析 .gitignore 文本,返回规则列表(保留顺序,最后匹配者生效)。"""
    rules: list[_Rule] = []
    for raw in text.splitlines():
        rule = _parse_rule(raw)
        if rule is not None:
            rules.append(rule)
    return rules


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把 glob 模式(如 **/*.py)转为锚定正则,供全路径匹配。

    语义接近 pathlib glob:** 跨目录,* 不跨 /。
    """
    return re.compile("^" + _translate(pattern) + "$")


class GitignoreMatcher:
    """基于项目根的 .gitignore 层级匹配器。

    判定路径是否被忽略:从根逐层收集各目录下的 .gitignore 规则(按 mtime
    缓存),按"父级在前、子级在后"的顺序对路径的每个前缀求值,最后一条
    匹配的模式决定结果(支持 ! 取反)。
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self._rules_cache: dict[str, tuple[Optional[int], list[_Rule]]] = {}

    def _rules_for(self, directory: Path) -> list[_Rule]:
        gitignore = directory / ".gitignore"
        try:
            st = gitignore.stat()
        except OSError:
            return []
        cached = self._rules_cache.get(str(gitignore))
        if cached is not None and cached[0] == st.st_mtime_ns:
            return cached[1]
        try:
            rules = parse_gitignore(gitignore.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            rules = []
        self._rules_cache[str(gitignore)] = (st.st_mtime_ns, rules)
        return rules

    def is_ignored(self, path: str | os.PathLike[str], is_dir: bool) -> bool:
        """判断 path 是否被 .gitignore 忽略;位于 root 之外的路径永不忽略。"""
        try:
            rel = Path(path).resolve().relative_to(self.root)
        except (ValueError, OSError):
            return False
        parts = rel.parts
        ignored = False
        for idx in range(len(parts)):
            base_dir = self.root.joinpath(*parts[:idx])
            rules = self._rules_for(base_dir)
            if not rules:
                continue
            suffix = PurePosixPath(*parts[idx:])
            for k in range(1, len(suffix.parts) + 1):
                prefix = PurePosixPath(*suffix.parts[:k])
                prefix_is_dir = (k < len(suffix.parts)) or is_dir
                for rule in rules:
                    if rule.matches(prefix, prefix_is_dir):
                        ignored = not rule.negated
        return ignored

    def walk(self, top: str | os.PathLike[str] = ".") -> Iterator[tuple[Path, list[Path], list[Path]]]:
        """迭代目录树,裁剪被忽略的目录并过滤被忽略的文件。

        top 相对 root 或为 root 之下路径;生成 (dir, dirs, files) 三元组,
        与 os.walk 相似但 dirs/files 为完整 Path,且已剔除被忽略项。
        """
        start = Path(top)
        if not start.is_absolute():
            start = self.root / start
        start = start.resolve()
        for dirpath, dirnames, filenames in os.walk(start):
            keep_dirs: list[str] = []
            keep_files: list[Path] = []
            for d in dirnames:
                dp = Path(dirpath) / d
                try:
                    if not self.is_ignored(dp, True):
                        keep_dirs.append(d)
                except OSError:
                    pass
            dirnames[:] = keep_dirs
            for f in filenames:
                fp = Path(dirpath) / f
                try:
                    if not self.is_ignored(fp, False):
                        keep_files.append(fp)
                except OSError:
                    pass
            yield Path(dirpath), [Path(dirpath) / d for d in keep_dirs], keep_files

    def glob(self, pattern: str, top: str | os.PathLike[str] = ".") -> list[Path]:
        """在 top 下按 glob 模式匹配文件,跳过被忽略的目录与文件。

        返回值已排序(mtime 降序)。支持 **/* 等递归模式。
        """
        rx = glob_to_regex(pattern)
        start = Path(top)
        if not start.is_absolute():
            start = self.root / start
        start = start.resolve()
        results: list[Path] = []
        for dirpath, dirs, files in self.walk(start):
            for d in dirs:
                try:
                    rel = d.relative_to(start)
                except ValueError:
                    rel = Path(d.name)
                if rx.match(rel.as_posix()):
                    results.append(d)
            for f in files:
                try:
                    rel = f.relative_to(start)
                except ValueError:
                    rel = Path(f.name)
                if rx.match(rel.as_posix()):
                    results.append(f)
        results.sort(key=lambda p: _mtime_or_zero(p), reverse=True)
        return results


def _mtime_or_zero(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
