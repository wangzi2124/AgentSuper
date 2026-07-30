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


def _ensure_safe(path: Path) -> None:
    """检查路径访问权限，不允许时抛出PermissionError或NeedsPermission异常。"""
    resolved = path.resolve()
    mgr = get_perm_mgr()
    decision = mgr.check(str(resolved), "write")
    if decision == "allow":
        return
    if decision == "deny":
        raise PermissionError(f"Access denied: '{path}' is outside workspace")
    raise NeedsPermission(str(resolved), "write")


def tool_ls(path: str = ".") -> str:
    """列出指定目录下的文件和子目录，显示类型、大小和修改时间。"""
    target = _resolve(path)
    _ensure_safe(target)
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
    _ensure_safe(target)
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
    _ensure_safe(target)
    if target.exists() and not overwrite:
        return f"Error: file already exists: {path} (use overwrite=True to overwrite, or edit_file to modify)"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
        action = "Overwritten" if target.exists() else "Created"
        return f"{action} {path} ({target.stat().st_size} bytes)"
    except Exception as e:
        return f"Error writing file: {e}"


def tool_edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """在文件中查找并替换指定字符串，支持单次替换或全部替换。"""
    target = _resolve(path)
    _ensure_safe(target)
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


def tool_glob(pattern: str) -> str:
    """在工作目录中按glob模式搜索文件，返回匹配的相对路径列表。"""
    matches = sorted(WORKSPACE.glob(pattern))
    if not matches:
        return f"No matches for: {pattern}"
    lines = [str(m.relative_to(WORKSPACE)) for m in matches]
    return "\n".join(lines)


def tool_grep(pattern: str, include: str = "", context: int = 0, count_only: bool = False, files_only: bool = False) -> str:
    """在工作目录的文本文件中按正则表达式搜索内容，支持文件过滤和上下文显示。"""
    import re
    use_re = re.compile(pattern, re.MULTILINE | re.DOTALL)
    file_pattern = include if include else "**/*"
    matched_any = False
    output: list[str] = []
    for f in sorted(WORKSPACE.glob(file_pattern)):
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
        rel = str(f.relative_to(WORKSPACE))
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


def tool_execute(command: str, timeout: int = 300, work_dir: str = ".") -> str:
    """执行shell命令并返回标准输出、标准错误和退出码。"""
    if timeout > 600:
        timeout = 600
    if timeout < 1:
        timeout = 5
    _check_command_allowed(command)
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
