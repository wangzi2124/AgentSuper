"""拆分模块 `execv`（含 _ALLOWED_COMMANDS、_BACKTICK_RE、_DANGEROUS_PATTERNS、_NET_COMMANDS、_WIN_CMD_BUILTINS、_WIN_SHIM_EXTS、_backtick_bodies、_check_command_allowed、_check_command_blacklist、_check_single_allowed、_needs_shell、_split_shell_segments、_ssrf_check_command、_validate_shell_command、_win_cmd_needs_shell、_win_which_cache）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──



import os

import re

import shlex

import shutil






from pathlib import Path

from typing import Optional


from app.permission import get_manager as get_perm_mgr, NeedsPermission, current_session_workspace

# ── 跨子模块依赖（自动生成）──

from .lexcmd import _REDIRECT_OPS
from .lexcmd import _SHELL_SEP
from .lexcmd import _cmd_lex
from .lexcmd import _cmd_split_shell_segments
from .lexcmd import _first_command
from .lexcmd import _is_redirect_token
from .lexcmd import _win_flag_split
from .workspace import _workspace

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──



# Whitelist of allowed commands for tool_execute to mitigate command injection risk.
# Only the base command (first token) is checked against this set.

_ALLOWED_COMMANDS = frozenset({
    "python", "python3", "node", "npm", "npx", "pip", "pip3",
    "git", "curl", "wget", "cat", "head", "tail", "less", "more",
    "type", "findstr", "ls", "dir", "find", "grep", "rg", "ag", "ack", "sed", "awk",
    "sort", "uniq", "wc", "cut", "tr", "echo", "printf",
    "cp", "mv", "rm", "mkdir", "rmdir", "touch", "chmod", "chown",
    "zip", "unzip", "tar", "gzip", "gunzip", "bzip2",
    "date", "whoami", "hostname", "uname", "df", "du", "free", "ps", "top",
    "which", "type", "file", "stat", "md5sum", "sha256sum",
    "diff", "patch", "comm", "cmp",
    "jq", "yq",
    "make", "cmake", "gcc", "g++", "clang", "rustc", "cargo",
    "go", "deno", "ts-node",
    "docker", "docker-compose",
    "ping", "nslookup", "dig", "traceroute",
    "ssh", "scp", "rsync",
    "ffprobe", "ffmpeg",
    "nproc", "nvidia-smi",
    "cmd", "powershell",
    "cd",
})

def _split_shell_segments(command: str) -> list[list[str]]:
    """把 shell 命令切分为简单命令 token 组（引号感知）。

    在 ; | || && & ( ) 处断开；重定向符及其目标附加到当前命令段；
    $(...) 中的子命令因 '(' 断开而自然成为独立段，从而被独立校验。

    Windows 下改用 cmd.exe 语义词法（_cmd_split_shell_segments）：正确处理
    `^` 转义、`\\` 非转义、`%VAR%`、引号内字面量等，使切分与真实执行一致。
    """
    if os.name == "nt":
        return _cmd_split_shell_segments(command)
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    tokens = list(lex)
    segments: list[list[str]] = []
    current: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            current.append(tok)
            continue
        if tok in _SHELL_SEP:
            if current:
                segments.append(current)
                current = []
            continue
        if tok in _REDIRECT_OPS:
            current.append(tok)
            skip_next = True
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments

_BACKTICK_RE = re.compile(r"`([^`]*)`")

# ── [git 写操作审批] ────────────────────────────────────────────────────────
# `git` 在 _ALLOWED_COMMANDS 里 → _check_single_allowed 直接早退，**从不调用**
# check_command，于是 `git reset --hard` / `git commit` / `git checkout .` 可以
# 无提示改写用户的仓库/index/HEAD，还会让本轮快照的 before_tree 与真实历史错位
# （restore 时 `git checkout <before_tree> -- <files>` 还原出与用户历史不符的内容）。
#
# 这里按**子命令**分流，而不是把 git 整个踢出白名单（那会连 `git status` /
# `git diff` 这类只读、且是本项目快照/文件核对主力的用法一起误伤）：
#   - 只读子命令 → 放行（不进审批）
#   - 其余一切（改写工作区/索引/HEAD/远端，含 add/commit/reset/checkout/
#     restore/clean/stash/rm/mv/apply/rebase/merge/push…）→ check_command → 前端审批
#   - 解析不出子命令 → 同样按写操作处理（fail-closed）
# 审批 key 用 `git <subcommand>` 而非裸 `git`：批准 `git add` 不会顺带放开
# `git reset`；持久化白名单 / 「允许并记住」也因此是**按子命令**粒度。

# 只读子命令白名单：仅列出不会写入任何仓库状态（工作区/索引/HEAD/refs）的命令。
# 刻意不含 branch/remote/tag/stash/config/reflog/symbolic-ref/update-ref 等
# 「同名既可读又可写」的复合命令 —— 一律走审批，由用户在弹窗里判断。
_GIT_READONLY_SUBCOMMANDS = frozenset({
    "blame", "cat-file", "check-attr", "check-ignore", "count-objects",
    "describe", "diff", "diff-files", "diff-index", "diff-tree",
    "for-each-ref", "grep", "help", "log", "ls-files", "ls-remote",
    "ls-tree", "merge-base", "name-rev", "rev-list", "rev-parse", "shortlog",
    "show", "show-branch", "show-ref", "status", "var", "verify-commit",
    "version", "whatchanged",
})

# 取值的全局选项（会吃掉下一个 token），解析子命令时必须成对跳过：
# `git -C repo status` / `git -c user.email=a@b commit`。
_GIT_GLOBAL_VALUE_FLAGS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--config-env", "--attr-source",
})

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_git_command(base_cmd: str) -> bool:
    """基命令是否为 git（含 `C:\\...\\git.exe` / `/usr/bin/git` 这样的显式路径）。"""
    name = base_cmd.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name == "git"


def _git_subcommand(seg: list[str]) -> Optional[str]:
    """从 git 命令段解析子命令（小写）；跳过 env 前缀、重定向符、基命令与全局选项。

    无法确定时返回 None（调用方按写操作处理）。
    """
    base_idx = -1
    for i, tok in enumerate(seg):
        if _ENV_ASSIGN_RE.match(tok) or tok == "$" or _is_redirect_token(tok):
            continue
        base_idx = i
        break
    if base_idx < 0:
        return None
    rest = seg[base_idx + 1:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("-"):
            i += 2 if tok in _GIT_GLOBAL_VALUE_FLAGS else 1
            continue
        return tok.lower()
    return None


def _check_git_write_approval(seg: list[str], sub: Optional[str], ask: bool = False) -> None:
    """git 改写类子命令走命令级审批（对齐 opencode：动仓库前先问用户）。

    allow → 放行；deny → 明确拒绝；ask → 抛 NeedsPermission 走前端审批弹窗。
    `ask=False` 的调用方保持原语义（只做白名单，不触发审批）。
    """
    if not ask:
        return
    key = f"git {sub}" if sub else "git"
    decision = get_perm_mgr().check_command(key)
    if decision == "allow":
        return
    if decision == "deny":
        raise ValueError(f"Command '{key}' is not allowed")
    raise NeedsPermission(key, "command", "tool_execute", {"command": " ".join(seg)})

def _backtick_bodies(command: str) -> list[str]:
    """提取命令中反引号命令替换的内部命令文本。"""
    return [m.group(1) for m in _BACKTICK_RE.finditer(command)]

def _check_single_allowed(base_cmd: str, cwd: str | None = None, ask: bool = False) -> None:
    """单个基命令的白名单校验（含路径解析规则，原 _check_command_allowed 核心）。"""
    if base_cmd.lower() in _ALLOWED_COMMANDS:
        return
    if "/" in base_cmd or "\\" in base_cmd:
        bases = [Path(cwd)] if cwd else []
        session = current_session_workspace()
        if session:
            bases.append(Path(session))
        bases.append(_workspace())
        for base in bases:
            candidate = (base / base_cmd).resolve()
            if candidate.is_file() and candidate.is_relative_to(base):
                return
        raise ValueError(
            f"Command '{base_cmd}' is not in the allowed whitelist (path must point to an existing file inside the workspace)"
        )
    # ask 模式：查询管理器（持久化白名单 + 临时授权），未命中则抛 NeedsPermission
    if ask:
        decision = get_perm_mgr().check_command(base_cmd)
        if decision == "allow":
            return
        if decision == "deny":
            raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")
        # decision == "ask"：落入 NeedsPermission 通用审批流程
        raise NeedsPermission(base_cmd, "command", "tool_execute", {"command": base_cmd})
    # 非 ask 模式：保持原 ValueError（兼容旧调用方，零回归）
    raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")

def _validate_shell_command(command: str, cwd: str | None = None, ask: bool = False) -> None:
    """tool_execute / 流式执行共用的完整安全校验：白名单 + 黑名单 + SSRF，逐段执行。

    - 反引号命令替换内部命令递归校验
    - 每个简单命令段的首命令过白名单（防 `cat x | evil` 绕过）
    - **git 子命令分流**：只读子命令放行，改写仓库/索引/HEAD 的走命令级审批（`_check_git_write_approval`）
    - 每段跑解释器内联黑名单与 SSRF
    """
    for inner in _backtick_bodies(command):
        _validate_shell_command(inner, cwd, ask=ask)
    segments = _split_shell_segments(command)
    if not segments:
        raise ValueError("Empty command")
    for seg in segments:
        base = _first_command(seg)
        if base is None:
            continue
        if _is_git_command(base):
            sub = _git_subcommand(seg)
            # 解析不出子命令（`git` 裸跑 / 全局选项吃掉了后续 token）→ 按写操作处理，走审批
            if sub is None or sub not in _GIT_READONLY_SUBCOMMANDS:
                _check_git_write_approval(seg, sub, ask=ask)
        _check_single_allowed(base, cwd, ask=ask)
        seg_str = " ".join(seg)
        _check_command_blacklist(seg_str)
        _ssrf_check_command(seg_str)



# Windows cmd 内建命令（无独立可执行文件，CreateProcess 无法直接启动，
# 必须经 cmd.exe 解释）。与 POSIX 无关，仅 os.name == "nt" 时参与判定。

_WIN_CMD_BUILTINS = frozenset({
    "assoc", "attrib", "break", "call", "cd", "chdir", "cls", "color", "copy",
    "date", "del", "dir", "echo", "endlocal", "erase", "exit", "for", "ftype",
    "goto", "if", "md", "mkdir", "move", "path", "pause", "popd", "prompt",
    "pushd", "rd", "rem", "ren", "rename", "rmdir", "set", "setlocal", "shift",
    "start", "time", "title", "type", "ver", "verify", "vol",
})

_WIN_SHIM_EXTS = frozenset({".cmd", ".bat", ".ps1"})


# 命中 exec 路径却无法被 CreateProcess 直接启动的基命令（缓存 which 结果，避免每轮扫描）。

_win_which_cache: dict[str, Optional[str]] = {}

def _win_cmd_needs_shell(command: str) -> bool:
    """Windows 下基命令是否需要真实 shell？

    - cmd 内建命令（echo/dir/type…）：无独立 exe，必须 cmd.exe 解释
    - npm/npx/yarn/pnpm 等 npm.cmd 垫片：which 解析为 .cmd/.bat/.ps1，CreateProcess 无法启动
    - 其余解析到 .exe 的可执行文件：走安全 exec 路径（零回归）
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    base = parts[0]
    if base.lower() in _WIN_CMD_BUILTINS:
        return True
    if "/" in base or "\\" in base:
        # 显式路径命令：按扩展名判定（.cmd/.bat/.ps1 需 shell，其余交给 exec + 解析）
        return Path(base).suffix.lower() in _WIN_SHIM_EXTS
    resolved = _win_which_cache.get(base)
    if resolved is None:
        resolved = shutil.which(base)
        _win_which_cache[base] = resolved
    if resolved is None:
        return False
    return Path(resolved).suffix.lower() in _WIN_SHIM_EXTS

def _needs_shell(command: str) -> bool:
    """命令是否需要真实 shell 执行？

    判定依据：
    - Windows (cmd.exe)：复用 _cmd_lex 判定 cmd_sep / 重定向 / %VAR% / 通配符 → 是
      （与真实执行语义一致：cmd 引号内同样展开 %VAR%，单引号是普通字符而非引号）
    - POSIX：引号外的 shell 语义（管道/重定向/&&/$VAR/反引号/通配符）→ 是
    - Windows 下基命令为 cmd 内建或 .cmd/.bat/.ps1 垫片（npm 等）→ 是
    - 其余保持安全的 shlex.split + exec 路径
    """
    if os.name == "nt":
        for tok, kind in _cmd_lex(command):
            if kind in ("cmd_sep", "redirect"):
                return True
            if kind == "word":
                # %VAR% 展开：cmd 在引号内同样展开 → 一律需要 shell
                if "%" in tok:
                    return True
                # cmd 对引号外的 *? 交给命令自身展开；引号内是字面量。
                # 保守起见引号外的 *? 一律走 shell（与历史行为一致，多走无害）
                if not tok.startswith('"') and any(ch in tok for ch in "*?"):
                    return True
        return _win_cmd_needs_shell(command)

    single = False
    double = False
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c == "'" and not double:
            single = not single
        elif c == '"' and not single:
            double = not double
        elif not single and not double:
            if c in "|&;<>()\n":
                return True
            if c in "$`*?[~":
                return True
        i += 1
    return False

def _check_command_allowed(command: str, cwd: str | None = None, ask: bool = False) -> None:
    """Check the base command against the whitelist. Raises ValueError if not allowed.

    兼容入口：调用完整校验（逐段）。若只需首 token 语义，请直接用 _validate_shell_command。
    白名单校验首个 token；若首 token 含路径分隔符（如 `.venv/Scripts/python.exe`），
    则放行能解析到工作区（会话目录或 backend/ 根）内真实文件的命令。
    """
    _validate_shell_command(command, cwd)



# 解释器类命令的 -c/-e/-Command 参数中禁止出现的高危模式

_DANGEROUS_PATTERNS = (
    # Python 任意代码执行 / 进程逃逸
    "os.system", "os.popen", "os.spawn", "os.startfile", "os.execl", "os.exec",
    "subprocess", "pty.spawn", "pty.openpty",
    "eval(", "exec(", "compile(", "globals()", "locals()",
    "__import__", "importlib", "runpy", "pickle", "marshal", "codecs.decode",
    # 网络访问 (绕过 SSRF 检查的通道)
    "socket.", "urllib", "requests.", "http.client", "aiohttp", "httpx",
    "ftplib", "telnetlib", "smtplib", "poplib", "imaplib", "xmlrpc",
    # 反序列化 / 本机渗透
    "base64", "ctypes", "win32api", "winreg", "win32con", "b64decode", "b64encode",
    "cryptography.", "ssl._create_default_context",
    # Windows PowerShell / cmd 高危原语
    "Invoke-Expression", "IEX", "Invoke-WebRequest", "IWR", "DownloadString",
    "DownloadFile", "WebClient", "Net.WebClient", "Start-Process",
    "Add-MpPreference", "shutdown", "reg add", "net user", "net localgroup",
    "whoami", "netsh", "taskkill", "format ", "del /f", "wmic", "sc create",
    # 常见外联工具 (内联代码里禁 curl/wget 防止 SSRF 绕过)
    "/dev/tcp", "/dev/udp", "curl", "wget",
)

def _check_command_blacklist(command: str) -> None:
    """对解释器类命令的 -c/-e/-Command 内联参数做危险模式检查，阻止任意代码执行。

    白名单只能校验首个 token，`python -c "import os; os.system(...)"` 可完全绕过，
    因此对 python/node/powershell/cmd 等解释器的内联代码参数额外做黑名单过滤。
    """
    parts = _win_flag_split(command)
    if not parts:
        return
    interp = Path(parts[0]).name.lower() or parts[0].lower()
    interpreter_flag: str | None = None
    if interp in ("python", "python3", "py", "python.exe", "py.exe"):
        interpreter_flag = "-c"
    elif interp in ("node", "node.exe"):
        interpreter_flag = "-e"
    elif interp in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        interpreter_flag = "-Command"
    elif interp in ("cmd", "cmd.exe"):
        interpreter_flag = "/c"
    if interpreter_flag is None:
        return
    flag = interpreter_flag.lower()
    i = 1
    while i < len(parts):
        if parts[i].lower() == flag:
            inline = " ".join(parts[i + 1:])
            lowered = inline.lower()
            for pat in _DANGEROUS_PATTERNS:
                if pat.lower() in lowered:
                    raise ValueError(
                        f"Command contains dangerous pattern '{pat}' in {interp} -c argument; "
                        "inline code execution is blocked"
                    )
            return
        i += 1



# 出站网络命令：对其 URL 参数做 SSRF 校验（curl/wget 任意 URL、ssh/scp/rsync 目标主机）

_NET_COMMANDS = frozenset({
    "curl", "wget", "ssh", "scp", "rsync", "ping", "nslookup", "dig", "traceroute",
})

def _ssrf_check_command(command: str) -> None:
    """对出站网络命令做 SSRF 校验：URL 目标为内网地址时拦截。"""
    from app.utils.ssrf import allow_internal, check_url, _host_is_internal

    parts = _win_flag_split(command)
    if not parts or parts[0].lower() not in _NET_COMMANDS:
        return
    if allow_internal():
        # SSRF_ALLOW_INTERNAL=true（本地调试）→ 放行内网；协议校验仍交给 check_url
        return
    for tok in parts[1:]:
        t = tok.lower()
        if "://" in t:
            check_url(tok)
            continue
        if t.startswith(("-", "=")):
            continue
        # 裸目标（curl 127.0.0.1 / curl localhost:8000 / ssh 10.0.0.1）
        candidate = t
        if ":" in t and not t.endswith(":"):
            candidate = t.rsplit(":", 1)[0]
        if candidate in ("", "-"):
            continue
        # scp/ssh 常带 user@ 前缀（user@10.0.0.9:/path）：先剥掉 userinfo 再判主机，
        # 否则 getaddrinfo 会拿整个 "user@host" 去解析 → gaierror → 误判为公网漏放。
        if "@" in candidate:
            candidate = candidate.rsplit("@", 1)[-1].strip("[]")
        if candidate in ("", "-"):
            continue
        if _host_is_internal(candidate):
            raise ValueError(
                f"Command targets internal address '{tok}' which is blocked by SSRF protection"
            )



__all__ = ["_ALLOWED_COMMANDS", "_BACKTICK_RE", "_DANGEROUS_PATTERNS", "_NET_COMMANDS", "_WIN_CMD_BUILTINS", "_WIN_SHIM_EXTS", "_backtick_bodies", "_check_command_allowed", "_check_command_blacklist", "_check_single_allowed", "_needs_shell", "_split_shell_segments", "_ssrf_check_command", "_validate_shell_command", "_win_cmd_needs_shell", "_win_which_cache"]
