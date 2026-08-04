import base64
import json
import os
import shlex
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.permission import get_manager as get_perm_mgr, NeedsPermission

WORKSPACE = Path(__file__).resolve().parents[2]
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".py", ".js", ".ts", ".vue", ".html", ".css", ".scss", ".less", ".sh", ".bat", ".ps1", ".env", ".env.example", ".ini", ".cfg", ".conf", ".toml", ".sql", ".sqlite"}
_PDF_EXTS = {".pdf"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
_DOC_EXTS = {".docx", ".xlsx", ".pptx"}
_MULTIMODAL_EXTS = _IMAGE_EXTS | _PDF_EXTS | _AUDIO_EXTS | _VIDEO_EXTS

_MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _resolve(path_str: str) -> Path:
    """将路径字符串解析为绝对路径，相对路径基于工作目录解析。"""
    p = Path(path_str)
    if not p.is_absolute():
        p = WORKSPACE / p
    return p.resolve()


def _ensure_safe(path: Path, operation: str = "write") -> None:
    """检查路径访问权限，不允许时抛出PermissionError或NeedsPermission异常。"""
    resolved = path.resolve()
    mgr = get_perm_mgr()
    decision = mgr.check(str(resolved), operation)
    if decision == "allow":
        return
    if decision == "deny":
        raise PermissionError(f"Access denied: '{path}' is outside workspace or protected")
    raise NeedsPermission(str(resolved), operation)


def _coerce_int(value, default: int = 0) -> int:
    """将任意输入安全转换为整数（LLM 可能以字符串形式传数值参数）。

    布尔值不是有效整数（True 在 int 强转下为 1），按默认值处理。
    """
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value, default: bool = False) -> bool:
    """将任意输入安全转换为布尔值，容忍 "true"/"1"/"yes" 等字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def tool_ls(path: str = ".") -> str:
    """列出指定目录下的文件和子目录，显示类型、大小和修改时间。"""
    target = _resolve(path)
    _ensure_safe(target, "read")
    if not target.is_dir():
        return f"Error: '{path}' is not a directory"
    rows = []
    for entry in sorted(target.iterdir()):
        try:
            st = entry.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            kind = "d" if entry.is_dir() else "f"
            rows.append(f"{kind} {size:>10}  {mtime}  {entry.name}")
        except OSError:
            rows.append(f"? {'':>10}  {'':19}  {entry.name}")
    return "\n".join(rows) if rows else "(empty)"


def tool_read_file(path: str, offset: int = 1, limit: int = 0) -> str:
    """读取文件内容，文本文件支持按行偏移和限制，多模态文件返回base64编码。"""
    target = _resolve(path)
    _ensure_safe(target, "read")
    offset = _coerce_int(offset, 1)
    limit = _coerce_int(limit, 0)
    if not target.is_file():
        return f"Error: file not found: {path}"
    ext = target.suffix.lower()
    if ext in _TEXT_EXTS:
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except Exception as e:
            return f"Error reading text file: {e}"
        if offset < 1:
            offset = 1
        start = offset - 1
        total = len(lines)
        if limit > 0:
            selected = lines[start:start + limit]
        else:
            selected = lines[start:]
        result = "".join(selected)
        info = f"--- {path} (lines {offset}-{min(offset + (limit or total) - 1, total)} of {total}) ---\n"
        return info + result
    try:
        raw = target.read_bytes()
        b64 = base64.b64encode(raw).decode("utf-8")
        mime = _MIME_MAP.get(ext, "application/octet-stream")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        return f"Error reading file: {e}"


def tool_write_file(path: str, content: str, overwrite: bool = False) -> str:
    """创建新文件并写入内容。overwrite=True 时允许覆盖已存在的文件。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    overwrite = _coerce_bool(overwrite)
    if target.exists() and not overwrite:
        return f"Error: file already exists: {path} (use overwrite=True to overwrite, or edit_file to modify)"
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        target.write_text(str(content), encoding="utf-8")
        action = "Overwritten" if existed else "Created"
        return f"{action} {path} ({target.stat().st_size} bytes)"
    except Exception as e:
        return f"Error writing file: {e}"


def tool_append_file(path: str, content: str) -> str:
    """向文件追加内容（文件不存在则创建）。用于分段写入大文件：先 tool_write_file 写首段，再多次 append。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        with open(target, "a", encoding="utf-8") as f:
            f.write(str(content))
        total = target.stat().st_size
        action = "Appended to" if existed else "Created"
        return f"{action} {path} ({total} bytes total)"
    except Exception as e:
        return f"Error appending to file: {e}"


def tool_edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """在文件中查找并替换指定字符串，支持单次替换或全部替换。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    replace_all = _coerce_bool(replace_all)
    if not target.is_file():
        return f"Error: file not found: {path}"
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"
    if replace_all:
        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        text = text.replace(old_string, new_string)
    else:
        if old_string not in text:
            return f"Error: old_string not found in {path}"
        count = 1
        text = text.replace(old_string, new_string, 1)
    try:
        target.write_text(text, encoding="utf-8")
        return f"Replaced {count} occurrence(s) in {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def tool_glob(pattern: str, root: str = ".") -> str:
    """在指定目录中按glob模式搜索文件，返回匹配路径列表（默认工作区为相对路径，自定义 root 为绝对路径）。"""
    root_path = _resolve(root)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return f"Error: '{root}' is not a directory"
    matches = sorted(root_path.glob(pattern))
    if not matches:
        return f"No matches for: {pattern}"
    if root_path == WORKSPACE:
        lines = [str(m.relative_to(WORKSPACE)) for m in matches]
    else:
        lines = [str(m.resolve()) for m in matches]
    return "\n".join(lines)


def tool_delete_file(path: str) -> str:
    """删除指定文件或空目录（仅限工作区内）。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    if not target.exists():
        return f"Error: not found: {path}"
    if target.is_dir():
        try:
            target.rmdir()
            return f"Deleted directory {path}"
        except OSError as e:
            return f"Error deleting directory: {e}"
    try:
        target.unlink()
        return f"Deleted {path}"
    except Exception as e:
        return f"Error deleting file: {e}"


def tool_rename_file(path: str, new_path: str) -> str:
    """重命名或移动文件/目录到新路径。"""
    src = _resolve(path)
    dst = _resolve(new_path)
    _ensure_safe(src, "write")
    _ensure_safe(dst, "write")
    if not src.exists():
        return f"Error: source not found: {path}"
    if dst.exists():
        return f"Error: destination already exists: {new_path}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return f"Renamed {path} -> {new_path}"
    except Exception as e:
        return f"Error renaming: {e}"


def tool_grep(pattern: str, include: str = "", context: int = 0, count_only: bool = False, files_only: bool = False, root: str = ".") -> str:
    """在指定目录的文本文件中按正则表达式搜索内容，支持文件过滤、上下文显示和目录范围。"""
    import re
    context = _coerce_int(context, 0)
    count_only = _coerce_bool(count_only)
    files_only = _coerce_bool(files_only)
    root_path = _resolve(root)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return f"Error: '{root}' is not a directory"
    use_re = re.compile(pattern, re.MULTILINE | re.DOTALL)
    file_pattern = include if include else "**/*"
    matched_any = False
    output: list[str] = []
    for f in sorted(root_path.glob(file_pattern)):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in _MULTIMODAL_EXTS:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = text.splitlines(keepends=True)
        found_positions: list[int] = []
        for m in use_re.finditer(text):
            line_start = text[:m.start()].count("\n")
            found_positions.append(line_start)
        if not found_positions:
            continue
        matched_any = True
        if root_path == WORKSPACE:
            rel = str(f.relative_to(WORKSPACE))
        else:
            rel = str(f.resolve())
        if files_only:
            output.append(rel)
            continue
        if count_only:
            output.append(f"{rel}: {len(found_positions)} match(es)")
            continue
        output.append(f"--- {rel} ---")
        seen_lines: set[int] = set()
        for lineno in found_positions:
            if lineno in seen_lines:
                continue
            seen_lines.add(lineno)
            start = max(0, lineno - context)
            end = min(len(lines), lineno + context + 1)
            for i in range(start, end):
                marker = ">" if i == lineno else " "
                output.append(f"{marker} {i+1:>6}: {lines[i].rstrip()}")
            output.append("")
    if not matched_any:
        return f"No matches for: {pattern}"
    return "\n".join(output).rstrip()


# Whitelist of allowed commands for tool_execute to mitigate command injection risk.
# Only the base command (first token) is checked against this set.
_ALLOWED_COMMANDS = frozenset({
    "python", "python3", "node", "npm", "npx", "pip", "pip3",
    "git", "curl", "wget", "cat", "head", "tail", "less", "more",
    "ls", "dir", "find", "grep", "rg", "ag", "ack", "sed", "awk",
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
})


def _check_command_allowed(command: str) -> None:
    """Check the base command against the whitelist. Raises ValueError if not allowed."""
    base_cmd = shlex.split(command)[0].lower() if command else ""
    if not base_cmd:
        raise ValueError("Empty command")
    if base_cmd not in _ALLOWED_COMMANDS:
        raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")


# 解释器类命令的 -c/-e/-Command 参数中禁止出现的高危模式
_DANGEROUS_PATTERNS = (
    "os.system", "os.popen", "subprocess", "os.exec", "eval(", "exec(",
    "__import__", "importlib", "pickle", "marshal",
    "socket.", "urllib", "requests.", "http.client", "aiohttp", "httpx",
    "base64", "ctypes", "win32api", "winreg", "b64decode",
    "Invoke-Expression", "IEX", "Invoke-WebRequest", "IWR", "DownloadString",
    "DownloadFile", "WebClient", "Net.WebClient", "Start-Process",
    "Add-MscProject", "shutdown", "reg add", "net user", "net localgroup",
    "whoami /all", "netsh", "taskkill", "format ", "del /f",
    "/dev/tcp", "/dev/udp", "curl", "wget",
)


def _check_command_blacklist(command: str) -> None:
    """对解释器类命令的 -c/-e/-Command 内联参数做危险模式检查，阻止任意代码执行。

    白名单只能校验首个 token，`python -c "import os; os.system(...)"` 可完全绕过，
    因此对 python/node/powershell/cmd 等解释器的内联代码参数额外做黑名单过滤。
    """
    parts = shlex.split(command)
    if not parts:
        return
    base_cmd = parts[0].lower()
    interpreter_flag: str | None = None
    if base_cmd in ("python", "python3", "py"):
        interpreter_flag = "-c"
    elif base_cmd == "node":
        interpreter_flag = "-e"
    elif base_cmd == "powershell":
        interpreter_flag = "-Command"
    elif base_cmd == "cmd":
        interpreter_flag = "/c"
    if interpreter_flag is None:
        return
    i = 1
    while i < len(parts):
        if parts[i].lower() == interpreter_flag:
            inline = " ".join(parts[i + 1:])
            lowered = inline.lower()
            for pat in _DANGEROUS_PATTERNS:
                if pat in lowered:
                    raise ValueError(
                        f"Command contains dangerous pattern '{pat}' in {base_cmd} -c argument; "
                        "inline code execution is blocked"
                    )
            return
        i += 1


def tool_execute(command: str, timeout: int = 300, work_dir: str = ".") -> str:
    """执行shell命令并返回标准输出、标准错误和退出码。"""
    timeout = _coerce_int(timeout, 300)
    if timeout > 600:
        timeout = 600
    if timeout < 1:
        timeout = 5
    _check_command_allowed(command)
    _check_command_blacklist(command)
    resolved_cwd = _resolve(work_dir)
    mgr = get_perm_mgr()
    decision = mgr.check(str(resolved_cwd), "execute")
    if decision == "ask":
        raise NeedsPermission(str(resolved_cwd), "execute", "tool_execute", {"command": command, "timeout": timeout, "work_dir": work_dir})
    if decision == "deny":
        return f"Error: access denied to directory '{work_dir}'"
    try:
        # Parse command into argument list to avoid shell injection
        args = shlex.split(command)
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(resolved_cwd),
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout.rstrip())
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        output = "\n".join(parts)
        rc = result.returncode
        header = f"Exit code: {rc}"
        if output:
            return f"{header}\n{output}"
        return header
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error executing command: {e}"
