"""拆分模块 `lexcmd`（含 _CMD_CMDSEP_CHARS、_CMD_REDIRECT_CHARS、_CMD_REDIRECT_OP_TOKENS、_REDIRECT_OPS、_SHELL_SEP、_WRITE_REDIRECT_OPS、_check_redirect_targets_permission、_cmd_lex、_cmd_split_shell_segments、_extract_redirect_targets、_first_command、_is_redirect_token、_win_flag_split）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──

import base64

import json

import os

import re

import shlex

import shutil

import signal

import stat

import subprocess

import time

from datetime import datetime

from pathlib import Path

from typing import Optional

from app.filesystem import GitignoreMatcher, ScanCache, get_project, glob_to_regex

from app.permission import get_manager as get_perm_mgr, NeedsPermission, current_session_workspace

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──



# ── shell 语义分段校验（opencode bash 语义对齐）─────────────────────────────
# 安全模型不变（白名单/黑名单/SSRF 硬校验），但支持管道/重定向/&&/$(...)/反引号：
# 把命令按 shell 简单命令切段，对【每段】的首命令做白名单校验，每段跑黑名单+SSRF，
# 防止 `cat x | evil` 之类绕过首 token 白名单。

# 分隔符：产生新的简单命令边界

_SHELL_SEP = {"|", "||", "&&", "&", ";", "(", ")"}

# 重定向符：其后一个 token 是重定向目标（文件名/文件描述符），不属于新命令

_REDIRECT_OPS = {">", ">>", "<", "<<", "<&", ">&", "2>", "2>>", "&>", "|&"}

# 写重定向符：其后 token 是文件写入目标（需做权限检查）

_WRITE_REDIRECT_OPS = {">", ">>", "2>", "2>>", "&>"}



# ── Windows cmd.exe 语义词法（对齐真实执行语义，而非 POSIX shlex）───────────
# 关键差异（决定校验与执行是否一致，否则出现白名单绕过或误拦）：
#   - `^X`（引号外）转义下一个字符 X → X 是字面量，不作为分隔符
#   - 反斜杠 `\` 不是转义符 → `\&` 在 cmd 是真正的命令分隔符（POSIX shlex 却当成
#     转义字面量合并成一个命令 → 校验漏掉第二命令的基命令，构成绕过）
#   - 引号内：`^` 与 `&|<>()` 都是字面量
#   - `%VAR%` 变量名：`%...%` 内整体为一个 token，不产生分隔符
#   - `!var!` 延迟展开：cmd /c 默认不开启 EnableDelayedExpansion → `!` 按普通字符
#     处理、绝不分词（避免把其中的分隔符漏判成字面量）

_CMD_CMDSEP_CHARS = set("&|()")

_CMD_REDIRECT_CHARS = set("<>")

def _cmd_lex(command: str) -> list[tuple[str, str]]:
    """cmd.exe 语义词法器 → [(token, kind)]，kind ∈ word|cmd_sep|redirect。

    仅用于【安全校验的切分】（白名单分段 / 重定向目标提取），不直接执行；
    语义与 `_run_shell` 的 cmd.exe 解释保持一致，杜绝「校验一套、执行另一套」。
    """
    i = 0
    n = len(command)
    in_quotes = False
    tokens: list[tuple[str, str]] = []
    word: list[str] = []

    def flush() -> None:
        nonlocal word
        if word:
            tokens.append(("".join(word), "word"))
            word = []

    while i < n:
        c = command[i]
        if in_quotes:
            # 引号内：^ 是字面量，&|<>() 也是字面量
            if c == '"':
                in_quotes = False
            word.append(c)
            i += 1
            continue
        # 引号外：^ 转义下一个字符（即使它是引号或元字符）
        if c == "^" and i + 1 < n:
            word.append(command[i + 1])
            i += 2
            continue
        if c == '"':
            in_quotes = True
            word.append(c)
            i += 1
            continue
        if c == "%":
            # cmd 变量展开 %VAR%：扫描到下一个 %；但区间内出现元字符 / 空白 /
            # 换行时不当作变量名（cmd 变量名不含这些字符），例如 `%a&evil%`
            # 中的 `&` 必须被切分，否则构成分段绕过（校验不切、执行却切）
            j = i + 1
            while j < n and command[j] != "%":
                if command[j] in _CMD_CMDSEP_CHARS or command[j] in _CMD_REDIRECT_CHARS or command[j] in " \t\r\n":
                    break
                j += 1
            if j < n and command[j] == "%":
                word.append(command[i:j + 1])
                i = j + 1
            else:
                word.append(c)
                i += 1
            continue
        if c in " \t":
            flush()
            i += 1
            continue
        if c in ";,":
            # cmd 的单词分隔符（不产生新命令边界）
            flush()
            i += 1
            continue
        if c in "\r\n":
            # cmd 把换行/回车当作命令分隔符（等价于 &）→ 切段，
            # 防止 `dir\n evil` 白名单分段绕过（校验不切、执行却切）
            flush()
            tokens.append((c, "cmd_sep"))
            i += 1
            continue
        two = command[i:i + 2]
        if two in ("&&", "||", "|&", ">>", "<<", "<&", ">&", "&>"):
            flush()
            tokens.append((two, "cmd_sep" if two in ("&&", "||", "|&") else "redirect"))
            i += 2
            continue
        if c in _CMD_CMDSEP_CHARS:
            flush()
            tokens.append((c, "cmd_sep"))
            i += 1
            continue
        if c in _CMD_REDIRECT_CHARS:
            # 可能的 fd 重定向：word 为纯数字则并入（2> / 2>> / 2>&1）
            if word and all(ch.isdigit() for ch in word):
                fd = "".join(word)
                word = []
                if command[i:i + 2] == ">>":
                    op = fd + ">>"
                    i += 2
                else:
                    op = fd + ">"
                    i += 1
                # 处理 2>&1 / 2>&2 等形式（fd 重复，非命令分隔）
                if i < n and command[i] == "&" and i + 1 < n and command[i + 1].isdigit():
                    j = i + 1
                    while j < n and command[j].isdigit():
                        j += 1
                    tokens.append((op + command[i:j], "redirect"))
                    i = j
                else:
                    tokens.append((op, "redirect"))
            else:
                flush()
                op = command[i:i + 2] if command[i:i + 2] == ">>" else c
                tokens.append((op, "redirect"))
                i += len(op)
            continue
        word.append(c)
        i += 1
    flush()
    return tokens

def _cmd_split_shell_segments(command: str) -> list[list[str]]:
    """Windows：按 cmd.exe 语义切分简单命令段（cmd_sep 处断开，重定向留在本段）。"""
    tokens = _cmd_lex(command)
    segments: list[list[str]] = []
    current: list[str] = []
    for tok, kind in tokens:
        if kind == "cmd_sep":
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments

def _win_flag_split(command: str) -> list[str]:
    """Windows：把命令按 cmd 语义拆成「实参 token」列表（仅 word，剔除重定向/分隔符）。

    供黑名单 / SSRF 等基于 POSIX shlex.split 的下游复用——POSIX shlex 无法解析
    cmd 特有的尾随反斜杠、单引号字面量等，会在 split 时抛 ValueError 误拦。
    """
    if os.name != "nt":
        try:
            return shlex.split(command)
        except ValueError:
            return []
    return [tok for tok, kind in _cmd_lex(command) if kind == "word"]

_CMD_REDIRECT_OP_TOKENS = frozenset({">", ">>", "<", "<<", "<&", ">&", "2>", "2>>", "&>", "|&"})

def _is_redirect_token(tok: str) -> bool:
    """判断 token 是否为 cmd 重定向符（含 fd 形式 2> / 2>> / 2>&1）。"""
    if tok in _CMD_REDIRECT_OP_TOKENS:
        return True
    if re.fullmatch(r"\d+>(>?)(&\d+)?", tok):
        return True
    return bool(re.fullmatch(r"\d+<&?\d*", tok))

def _first_command(seg: list[str]) -> Optional[str]:
    """取简单命令段的基命令名：跳过环境变量赋值前缀（FOO=bar ...）、命令替换 `$` 与重定向符。"""
    for tok in seg:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            continue
        if tok == "$":
            continue
        if _is_redirect_token(tok):
            continue
        return tok
    return None

def _is_write_redirect_token(tok: str) -> bool:
    """判断 token 是否为「写文件」重定向（>、>>、&>、2>、2>>、3>…）。

    输入重定向（<、<<、<&）与 fd 复制（2>&1、1<&2、>&）不产生文件写入目标，
    不应做写权限检查（修复前 Windows 分支把 `< in.txt` 也当写目标过度校验）。
    """
    if tok in (">", ">>", "&>"):
        return True
    return bool(re.fullmatch(r"\d+>(>?)", tok))

def _extract_redirect_targets(command: str) -> list[str]:
    """从 shell 命令中提取写重定向的目标文件路径。

    解析 >、>>、2>、2>>、&> 重定向操作符后的 token 作为文件写入目标。
    输入重定向（<、<<）和管道（|）不产生文件写入，忽略。
    """
    if os.name == "nt":
        # Windows：用 cmd 语义词法提取，避免 POSIX shlex 把 `\>` 当成转义字面量
        tokens = _cmd_lex(command)
        targets: list[str] = []
        for idx, (tok, kind) in enumerate(tokens):
            if kind != "redirect" or not _is_write_redirect_token(tok):
                continue
            for nxt, nkind in tokens[idx + 1:]:
                if nkind in ("word",):
                    targets.append(nxt)
                    break
                if nkind in ("cmd_sep",):
                    break
        return targets
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return []
    targets: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            # 跳过文件描述符数字（如 2>/dev/null 中的 /dev/null 也需要检查）
            targets.append(tok)
            continue
        if tok in _WRITE_REDIRECT_OPS:
            skip_next = True
            continue
    return targets

def _check_redirect_targets_permission(command: str, resolved_cwd: Path) -> None:
    """检查 shell 命令中写重定向目标文件的权限。

    对每个重定向目标路径解析并校验写权限；外部路径抛出 NeedsPermission。
    """
    targets = _extract_redirect_targets(command)
    if not targets:
        return
    mgr = get_perm_mgr()
    for target in targets:
        # 跳过 /dev/null 等特殊设备文件
        if target.startswith("/dev/"):
            continue
        # 解析为绝对路径
        t = Path(target)
        if not t.is_absolute():
            t = resolved_cwd / t
        resolved = t.resolve()
        decision = mgr.check(str(resolved), "write")
        if decision == "ask":
            raise NeedsPermission(str(resolved), "write", "tool_execute", {"command": command, "redirect_target": target})
        if decision == "deny":
            raise PermissionError(f"Access denied: redirect target '{target}' is outside workspace or protected")



__all__ = ["_CMD_CMDSEP_CHARS", "_CMD_REDIRECT_CHARS", "_CMD_REDIRECT_OP_TOKENS", "_REDIRECT_OPS", "_SHELL_SEP", "_WRITE_REDIRECT_OPS", "_check_redirect_targets_permission", "_cmd_lex", "_cmd_split_shell_segments", "_extract_redirect_targets", "_first_command", "_is_redirect_token", "_is_write_redirect_token", "_win_flag_split"]
