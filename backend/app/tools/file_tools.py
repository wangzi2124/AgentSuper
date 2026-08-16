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

# 回退基准：项目上下文未初始化时使用 backend/（历史行为）
_WORKSPACE_FALLBACK = Path(__file__).resolve().parents[2]

_matcher_cache: dict[str, GitignoreMatcher] = {}


def _env(title: str, output: str, **metadata) -> dict:
    """工具结果信封（对齐 opencode Tool.execute 返回的 {title, metadata, output}）。

    调用方（graph._execute_tool / sub_tools.run_tool）用 unwrap() 提取 output 喂给 LLM；
    信封中的 metadata 可承载 preview/display 等结构化信息供前端展示。
    """
    return {"title": title, "metadata": metadata, "output": output}


def unwrap(result: object) -> str:
    """从信封结构提取 output 字符串；非信封直接 str()（兼容旧返回）。"""
    if isinstance(result, dict) and "output" in result:
        return str(result["output"])
    return str(result)


def _gitignore_matcher() -> GitignoreMatcher | None:
    """按 worktree 惰性构建 .gitignore 匹配器(含 .gitignore 文件 mtime 缓存)。"""
    ws = _workspace()
    key = str(ws)
    matcher = _matcher_cache.get(key)
    if matcher is None:
        try:
            matcher = GitignoreMatcher(ws)
        except Exception:
            matcher = None
        _matcher_cache[key] = matcher
    return matcher


def _gitignore_checker(path: Path, is_dir: bool) -> bool:
    matcher = _gitignore_matcher()
    if matcher is None:
        return False
    try:
        return matcher.is_ignored(path, is_dir)
    except OSError:
        return False


_scan_cache = ScanCache(ignored_checker=_gitignore_checker)


def _workspace() -> Path:
    """统一路径基准：git worktree（仓库根）。未初始化项目上下文时回退 backend/。

    对应 opencode instance-context.ts 的 worktree（git root）：相对路径、glob/grep
    输出、命令校验均以 worktree 为基准，而不是硬编码 backend/。
    """
    try:
        return Path(get_project().worktree)
    except Exception:
        return _WORKSPACE_FALLBACK
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

DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_LINE_SUFFIX = f"... (line truncated to {MAX_LINE_LENGTH} chars)"
MAX_BYTES = 50 * 1024
MAX_BYTES_LABEL = f"{MAX_BYTES // 1024} KB"
SAMPLE_BYTES = 4096

_BINARY_EXTS = frozenset({
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar", ".war",
    ".7z", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp", ".bin", ".dat",
    ".obj", ".o", ".a", ".lib", ".wasm", ".pyc", ".pyo",
})


def _resolve(path_str: str) -> Path:
    """将路径字符串解析为绝对路径，相对路径基于当前会话工作目录解析。

    [会话目录] 本会话绑定了工作目录（opencode ctx.directory）时，相对路径
    以该目录为基准；否则回退到项目 worktree（git 仓库根）。
    """
    p = Path(path_str)
    if not p.is_absolute():
        base = current_session_workspace() or str(_workspace())
        p = Path(base) / p
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


def _is_read_allowed(path: Path) -> bool:
    """判断路径是否允许读取（工作区内敏感文件/系统路径/未授权外部路径返回 False）。"""
    mgr = get_perm_mgr()
    return mgr.check(str(path), "read") == "allow"


def tool_ls(path: str = ".") -> dict:
    """列出指定目录下的文件和子目录，显示类型、大小和修改时间。

    与 opencode list 语义对齐：被 .gitignore 忽略的项（如 node_modules/.venv）不列出；
    worktree 之外的路径（自定义 root）不做忽略过滤。
    """
    target = _resolve(path)
    _ensure_safe(target, "read")
    if not target.is_dir():
        return _env("ls", f"Error: '{path}' is not a directory", error=True)
    rows = []
    for node in _scan_cache.list_dir(target):
        if node.ignored:
            continue
        entry = Path(node.path)
        try:
            st = entry.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            kind = "d" if node.type == "dir" else "f"
            rows.append(f"{kind} {size:>10}  {mtime}  {node.name}")
        except OSError:
            rows.append(f"? {'':>10}  {'':19}  {node.name}")
    return _env("ls", "\n".join(rows) if rows else "(empty)", entries=len(rows), path=str(target))


def _is_binary(path: Path) -> bool:
    """检测文件是否为二进制（opencode read.ts 同款逻辑：扩展名黑名单 + NUL 字节 + 非打印字符占比）。

    只读前 SAMPLE_BYTES(4KB) 样本判定，避免大文件整读。
    """
    ext = path.suffix.lower()
    if ext in _BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            sample = f.read(SAMPLE_BYTES)
    except OSError:
        return False
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    non_printable = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return non_printable / len(sample) > 0.3


def _file_not_found_suggestion(path_str: str, target: Path) -> str:
    """文件不存在时返回相似文件名建议（opencode read.ts 同款）。"""
    parent = target.parent
    base = target.name.lower()
    try:
        entries = [e.name for e in parent.iterdir()]
    except OSError:
        entries = []
    suggestions = [
        str(parent / entry) for entry in entries
        if entry.lower().find(base) != -1 or base.find(entry.lower()) != -1
    ][:3]
    if suggestions:
        return f"File not found: {path_str}\n\nDid you mean one of these?\n" + "\n".join(suggestions)
    return f"File not found: {path_str}"


def tool_read_file(path: str, offset: int = 1, limit: int = 0) -> dict:
    """读取文件内容，文本文件按行号格式返回并支持行偏移/限制，图片/PDF/音视频返回base64。

    与 opencode read.ts 对齐：limit 未传/<=0 时默认 DEFAULT_READ_LIMIT(2000) 行，
    输出受 MAX_BYTES(50KB) 字节硬上限约束（超出即截断并提示续读），
    offset 越界时报错。行级流式读取，不整文件载入内存。
    目录也支持读取：列出条目（子目录带尾部 '/'）。
    """
    target = _resolve(path)
    _ensure_safe(target, "read")
    offset = _coerce_int(offset, 1)
    limit = _coerce_int(limit, 0)
    if limit <= 0:
        limit = DEFAULT_READ_LIMIT
    if target.is_dir():
        return _list_directory(target, path, offset, limit)
    if not target.is_file():
        return _file_not_found_envelope(path, target)
    ext = target.suffix.lower()
    if ext in _MULTIMODAL_EXTS:
        try:
            raw = target.read_bytes()
            b64 = base64.b64encode(raw).decode("utf-8")
            mime = _MIME_MAP.get(ext, "application/octet-stream")
            return _env("read", f"data:{mime};base64,{b64}", mime=mime, truncated=False)
        except Exception as e:
            return _env("read", f"Error reading file: {e}", error=True)
    if ext not in _TEXT_EXTS and _is_binary(target):
        return _env("read", f"Cannot read binary file: {path}", error=True)
    start = offset - 1
    selected: list[str] = []
    count = 0
    bytes_used = 0
    cut = False
    more = False
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            for text in f:
                text = text.rstrip("\r\n")
                count += 1
                if count <= start:
                    continue
                if len(selected) >= limit:
                    more = True
                    continue
                line = text if len(text) <= MAX_LINE_LENGTH else text[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
                size = len(line.encode("utf-8")) + (1 if selected else 0)
                if bytes_used + size > MAX_BYTES:
                    cut = True
                    more = True
                    break
                selected.append(line)
                bytes_used += size
    except Exception as e:
        return _env("read", f"Error reading text file: {e}", error=True)
    if count < offset and not (count == 0 and offset == 1):
        return _env("read", f"Offset {offset} is out of range for this file ({count} lines)", error=True)
    content_lines = []
    for i, line in enumerate(selected):
        line_no = offset + i
        content_lines.append(f"{line_no}: {line}")
    content = "\n".join(content_lines)
    last_read = offset + len(selected) - 1
    next_offset = last_read + 1
    if cut:
        footer = f"\n\n(Output capped at {MAX_BYTES_LABEL}. Showing lines {offset}-{last_read}. Use offset={next_offset} to continue.)"
    elif more:
        footer = f"\n\n(Showing lines {offset}-{last_read} of {count}. Use offset={next_offset} to continue.)"
    else:
        footer = f"\n\n(End of file - total {count} lines)"
    truncated = bool(cut or more)
    return _env(
        "read",
        f"<file>\n{content}{footer}\n</file>",
        truncated=truncated,
        line_start=offset,
        line_end=last_read,
        total_lines=count,
        path=str(target),
    )


def _list_directory(target: Path, path_str: str, offset: int, limit: int) -> dict:
    """读取目录：列出条目（opencode read 目录语义：子目录带尾部 '/'，排序 dir 在前）。"""
    entries = _scan_cache.list_dir(target)
    rows: list[dict] = []
    for node in entries:
        if node.ignored:
            continue
        display = node.name + ("/" if node.type == "dir" else "")
        rows.append({"name": node.name, "type": node.type, "display": display})
    rows.sort(key=lambda r: (0 if r["type"] == "dir" else 1, r["name"].lower()))
    start = offset - 1
    sliced = rows[start:start + limit]
    truncated = start + len(sliced) < len(rows)
    lines = [r["display"] for r in sliced]
    note = (
        f"\n(Showing {len(sliced)} of {len(rows)} entries. Use offset={offset + len(sliced)} to continue.)"
        if truncated
        else f"\n({len(rows)} entries)"
    )
    output = [
        f"<path>{target}</path>",
        "<type>directory</type>",
        "<entries>",
        "\n".join(lines),
        note,
        "</entries>",
    ]
    return _env(
        "read",
        "\n".join(output),
        truncated=truncated,
        entries=len(rows),
        shown=len(sliced),
        path=str(target),
    )


def _file_not_found_envelope(path_str: str, target: Path) -> dict:
    return _env("read", _file_not_found_suggestion(path_str, target), error=True)


def _detect_line_ending(text: str) -> str:
    """检测文本行尾（opencode edit.ts detectLineEnding 同款）：\r\n 出现次数多于 \n 视为 CRLF。"""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _normalize_line_endings(text: str) -> str:
    """将 CRLF 归一化为 LF（opencode edit.ts normalizeLineEndings 同款）。"""
    return text.replace("\r\n", "\n")


def _convert_line_ending(text: str, ending: str) -> str:
    """将 LF 行尾统一转换为目标行尾（opencode edit.ts convertToLineEnding 同款）。"""
    if ending == "\n":
        return text
    return text.replace("\n", ending)


def _read_text_raw(path: Path) -> tuple[str, bool]:
    """以 newline="" 原样读取文本（不做 universal newlines 转换），返回 (剥离BOM后的文本, 是否有BOM)。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    has_bom = text.startswith("\ufeff")
    return (text[1:] if has_bom else text), has_bom


def _write_text_raw(path: Path, text: str, has_bom: bool = False) -> None:
    """以 newline="" 原样写入文本（不做 os.linesep 转换），可选补回 UTF-8 BOM。"""
    if has_bom and not text.startswith("\ufeff"):
        text = "\ufeff" + text
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def tool_write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """创建新文件并写入内容。overwrite=True 时允许覆盖已存在的文件。

    行尾按内容原样写入（newline=""），不做系统换行符转换。
    """
    target = _resolve(path)
    _ensure_safe(target, "write")
    overwrite = _coerce_bool(overwrite)
    if target.exists() and not overwrite:
        return _env("write", f"Error: file already exists: {path} (use overwrite=True to overwrite, or edit_file to modify)", error=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        _write_text_raw(target, str(content))
        action = "Overwritten" if existed else "Created"
        _scan_cache.invalidate(target.parent)
        size = target.stat().st_size
        return _env("write", f"{action} {path} ({size} bytes)", path=str(target), size=size, action=action.lower())
    except Exception as e:
        return _env("write", f"Error writing file: {e}", error=True)


def tool_append_file(path: str, content: str) -> dict:
    """向文件追加内容（文件不存在则创建）。用于分段写入大文件：先 tool_write_file 写首段，再多次 append。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    try:
        with open(target, "a", encoding="utf-8", newline="") as f:
            f.write(str(content))
        total = target.stat().st_size
        action = "Appended to" if existed else "Created"
        _scan_cache.invalidate(target.parent)
        return _env("append", f"{action} {path} ({total} bytes total)", path=str(target), size=total)
    except Exception as e:
        return _env("append", f"Error appending to file: {e}", error=True)


def _edit_levenshtein(a: str, b: str) -> int:
    """Levenshtein 距离（opencode edit.ts levenshtein 移植）。"""
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(b)]


def _edit_line_positions(lines, start_line, end_line) -> tuple[int, int]:
    """计算 lines[start_line..end_line] 在整体字符串中的 [start, end) 位置。"""
    start = 0
    for k in range(start_line):
        start += len(lines[k]) + 1
    end = start
    for k in range(start_line, end_line + 1):
        end += len(lines[k])
        if k < end_line:
            end += 1
    return start, end


def _edit_simple_replacer(content: str, find: str):
    yield find


def _edit_line_trimmed_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    for i in range(0, len(original_lines) - len(search_lines) + 1):
        matches = all(
            original_lines[i + j].strip() == search_lines[j].strip()
            for j in range(len(search_lines))
        )
        if matches:
            start, end = _edit_line_positions(original_lines, i, i + len(search_lines) - 1)
            yield content[start:end]


def _edit_block_anchor_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if len(search_lines) < 3:
        return
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    first_line_search = search_lines[0].strip()
    last_line_search = search_lines[-1].strip()
    search_block_size = len(search_lines)

    candidates: list[tuple[int, int]] = []
    for i in range(len(original_lines)):
        if original_lines[i].strip() != first_line_search:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last_line_search:
                candidates.append((i, j))
                break

    if not candidates:
        return

    if len(candidates) == 1:
        start_line, end_line = candidates[0]
        actual_block_size = end_line - start_line + 1
        similarity = 0.0
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)
        if lines_to_check > 0:
            j = 1
            while j < search_block_size - 1 and j < actual_block_size - 1:
                original_line = original_lines[start_line + j].strip()
                search_line = search_lines[j].strip()
                max_len = max(len(original_line), len(search_line))
                if max_len > 0:
                    distance = _edit_levenshtein(original_line, search_line)
                    similarity += (1 - distance / max_len) / lines_to_check
                    if similarity >= 0.0:
                        break
                j += 1
        else:
            similarity = 1.0
        if similarity >= 0.0:
            start, end = _edit_line_positions(original_lines, start_line, end_line)
            yield content[start:end]
        return

    best: tuple[int, int] | None = None
    max_similarity = -1.0
    for start_line, end_line in candidates:
        actual_block_size = end_line - start_line + 1
        similarity = 0.0
        lines_to_check = min(search_block_size - 2, actual_block_size - 2)
        if lines_to_check > 0:
            j = 1
            while j < search_block_size - 1 and j < actual_block_size - 1:
                original_line = original_lines[start_line + j].strip()
                search_line = search_lines[j].strip()
                max_len = max(len(original_line), len(search_line))
                if max_len > 0:
                    similarity += 1 - _edit_levenshtein(original_line, search_line) / max_len
                j += 1
            similarity /= lines_to_check
        else:
            similarity = 1.0
        if similarity > max_similarity:
            max_similarity = similarity
            best = (start_line, end_line)
    if max_similarity >= 0.3 and best:
        start, end = _edit_line_positions(original_lines, best[0], best[1])
        yield content[start:end]


def _edit_whitespace_normalized_replacer(content: str, find: str):
    import re
    normalize_ws = lambda text: re.sub(r"\s+", " ", text).strip()
    normalized_find = normalize_ws(find)
    lines = content.split("\n")
    for line in lines:
        if normalize_ws(line) == normalized_find:
            yield line
        else:
            normalized_line = normalize_ws(line)
            if normalized_find and normalized_line.find(normalized_find) != -1:
                words = find.strip().split()
                if words:
                    pattern = r"\s+".join(re.escape(w) for w in words)
                    try:
                        m = re.search(pattern, line)
                        if m:
                            yield m.group(0)
                    except re.error:
                        pass
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(0, len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if normalize_ws(block) == normalized_find:
                yield block


def _edit_indentation_flexible_replacer(content: str, find: str):
    import re
    def remove_indentation(text: str) -> str:
        lines = text.split("\n")
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return text
        min_indent = min(
            len(m.group(1)) if (m := re.match(r"^(\s*)", l)) else 0 for l in non_empty
        )
        return "\n".join(l if not l.strip() else l[min_indent:] for l in lines)

    normalized_find = remove_indentation(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(0, len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i:i + len(find_lines)])
        if remove_indentation(block) == normalized_find:
            yield block


def _edit_escape_normalized_replacer(content: str, find: str):
    import re
    _ESCAPE_MAP = {
        "n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"',
        "`": "`", "\\": "\\", "$": "$", "\n": "\n",
    }
    def unescape_string(s: str) -> str:
        return re.sub(r"\\([ntr'\"`\\\n$])", lambda m: _ESCAPE_MAP.get(m.group(1), m.group(0)), s)

    unescaped_find = unescape_string(find)
    if content.find(unescaped_find) != -1:
        yield unescaped_find
    lines = content.split("\n")
    find_lines = unescaped_find.split("\n")
    for i in range(0, len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if unescape_string(block) == unescaped_find:
            yield block


def _edit_trimmed_boundary_replacer(content: str, find: str):
    trimmed_find = find.strip()
    if trimmed_find == find:
        return
    if content.find(trimmed_find) != -1:
        yield trimmed_find
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(0, len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i:i + len(find_lines)])
        if block.strip() == trimmed_find:
            yield block


def _edit_context_aware_replacer(content: str, find: str):
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines and find_lines[-1] == "":
        find_lines.pop()
    content_lines = content.split("\n")
    first_line = find_lines[0].strip()
    last_line = find_lines[-1].strip()
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first_line:
            continue
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() != last_line:
                continue
            block_lines = content_lines[i:j + 1]
            block = "\n".join(block_lines)
            if len(block_lines) == len(find_lines):
                matching = 0
                total_non_empty = 0
                for k in range(1, len(block_lines) - 1):
                    block_line = block_lines[k].strip()
                    find_line = find_lines[k].strip()
                    if block_line or find_line:
                        total_non_empty += 1
                        if block_line == find_line:
                            matching += 1
                if total_non_empty == 0 or matching / total_non_empty >= 0.5:
                    yield block
            break


def _edit_multi_occurrence_replacer(content: str, find: str):
    start_index = 0
    while True:
        index = content.find(find, start_index)
        if index == -1:
            break
        yield find
        start_index = index + len(find)


_EDIT_REPLACERS = [
    _edit_simple_replacer,
    _edit_line_trimmed_replacer,
    _edit_block_anchor_replacer,
    _edit_whitespace_normalized_replacer,
    _edit_indentation_flexible_replacer,
    _edit_escape_normalized_replacer,
    _edit_trimmed_boundary_replacer,
    _edit_context_aware_replacer,
    _edit_multi_occurrence_replacer,
]


def _edit_replace(content: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """opencode edit.ts replace() 移植：模糊匹配 + 多处匹配时报错（除非 replace_all）。"""
    if old_string == new_string:
        raise ValueError("oldString and newString must be different")

    not_found = True
    for replacer in _EDIT_REPLACERS:
        for search in replacer(content, old_string):
            index = content.find(search)
            if index == -1:
                continue
            not_found = False
            if replace_all:
                return content.replace(search, new_string)
            last_index = content.rfind(search)
            if index != last_index:
                continue
            return content[:index] + new_string + content[index + len(search):]
    if not_found:
        raise ValueError("oldString not found in content")
    raise ValueError(
        "Found multiple matches for oldString. Provide more surrounding lines in oldString to identify the correct match."
    )


def tool_edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict:
    """在文件中查找并替换指定字符串。支持模糊匹配；oldString 匹配到多处时报错（除非 replace_all=True）。

    与 opencode edit.ts 对齐：读取/写入均保留原文件行尾（LF 或 CRLF）与 UTF-8 BOM；
    old/new 字符串会归一化后转换到目标文件的行尾再做匹配，避免换行符静默损坏。
    """
    target = _resolve(path)
    _ensure_safe(target, "write")
    replace_all = _coerce_bool(replace_all)
    if not target.is_file():
        return _env("edit", f"File {path} not found", error=True)
    try:
        text, has_bom = _read_text_raw(target)
    except Exception as e:
        return _env("edit", f"Error reading file: {e}", error=True)
    ending = _detect_line_ending(text)
    try:
        if old_string == "":
            content_new = _convert_line_ending(_normalize_line_endings(new_string), ending)
        else:
            old = _convert_line_ending(_normalize_line_endings(old_string), ending)
            replacement = _convert_line_ending(_normalize_line_endings(new_string), ending)
            content_new = _edit_replace(text, old, replacement, replace_all)
    except ValueError as e:
        return _env("edit", f"Error: {e}", error=True)
    try:
        _write_text_raw(target, content_new, has_bom)
        _scan_cache.invalidate(target.parent)
        return _env("edit", f"Edited {path}", path=str(target), replace_all=replace_all)
    except Exception as e:
        return _env("edit", f"Error writing file: {e}", error=True)


def tool_glob(pattern: str, path: str = ".") -> dict:
    """在指定目录中按glob模式搜索文件，返回匹配路径列表（opencode glob 语义：绝对路径，No files found 表示无结果）。"""
    root_path = _resolve(path)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return _env("glob", f"Error: '{root}' is not a directory", error=True)
    matcher = _gitignore_matcher()
    if matcher is not None:
        matches = [m for m in matcher.glob(pattern, top=root_path) if _is_read_allowed(m)]
    else:
        matches = [m for m in root_path.glob(pattern) if _is_read_allowed(m)]
    if not matches:
        return _env("glob", "No files found", matches=0)
    matches.sort(key=lambda m: os.path.getmtime(m), reverse=True)
    # 对齐 opencode glob.ts：输出绝对路径
    lines = [str(m.resolve()) for m in matches[:100]]
    truncated = len(matches) > 100
    if truncated:
        lines.append("... and {} more".format(len(matches) - 100))
        lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
    return _env(
        "glob",
        "\n".join(lines),
        matches=min(len(matches), 100),
        total_matches=len(matches),
        truncated=truncated,
    )


def tool_delete_file(path: str) -> dict:
    """删除指定文件或空目录（仅限工作区内）。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    if not target.exists():
        return _env("delete", f"Error: not found: {path}", error=True)
    if target.is_dir():
        try:
            target.rmdir()
            _scan_cache.invalidate(target.parent)
            return _env("delete", f"Deleted directory {path}", path=str(target), kind="dir")
        except OSError as e:
            return _env("delete", f"Error deleting directory: {e}", error=True)
    try:
        target.unlink()
        _scan_cache.invalidate(target.parent)
        return _env("delete", f"Deleted {path}", path=str(target), kind="file")
    except Exception as e:
        return _env("delete", f"Error deleting file: {e}", error=True)


def tool_rename_file(path: str, new_path: str) -> dict:
    """重命名或移动文件/目录到新路径。"""
    src = _resolve(path)
    dst = _resolve(new_path)
    _ensure_safe(src, "write")
    _ensure_safe(dst, "write")
    if not src.exists():
        return _env("rename", f"Error: source not found: {path}", error=True)
    if dst.exists():
        return _env("rename", f"Error: destination already exists: {new_path}", error=True)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        _scan_cache.invalidate(src.parent)
        _scan_cache.invalidate(dst.parent)
        return _env("rename", f"Renamed {path} -> {new_path}", src=str(src), dst=str(dst))
    except Exception as e:
        return _env("rename", f"Error renaming: {e}", error=True)


def _patch_split_sections(text: str) -> list[tuple[str, str, str]]:
    """解析 apply_patch 补丁文本，返回 [(action, path, body)]。

    action ∈ add/update/delete；body 为文件操作体的原始行（不含头部）。
    对齐 apply_patch.txt 的 Begin/End Patch + Add/Delete/Update File 语义。
    """
    lines = text.splitlines()
    sections: list[tuple[str, str, str]] = []
    current: str | None = None
    current_path = ""
    body: list[str] = []

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            sections.append((current, current_path, "\n".join(body).rstrip("\n")))
            body = []

    for line in lines:
        stripped = line.strip()
        if stripped == "*** Begin Patch":
            continue
        if stripped == "*** End Patch":
            break
        if stripped.startswith("*** Add File:"):
            flush()
            current = "add"
            current_path = stripped[len("*** Add File:"):].strip()
            continue
        if stripped.startswith("*** Delete File:"):
            flush()
            current = "delete"
            current_path = stripped[len("*** Delete File:"):].strip()
            continue
        if stripped.startswith("*** Update File:"):
            flush()
            current = "update"
            current_path = stripped[len("*** Update File:"):].strip()
            continue
        if stripped.startswith("*** Move to:"):
            # 对齐 opencode：move 暂不支持 → 作为更新操作的一部分忽略
            continue
        if current is not None:
            body.append(line)
    flush()
    return sections


def _patch_add_body(body: str) -> str:
    """add 文件体：每行必须 + 前缀，去除前缀并补结尾换行。"""
    out_lines: list[str] = []
    for raw in body.splitlines():
        if raw.startswith("+"):
            out_lines.append(raw[1:])
        else:
            out_lines.append(raw)
    text = "\n".join(out_lines)
    return text if text.endswith("\n") else text + "\n"


def _patch_update_hunks(body: str) -> list[tuple[str, str]]:
    """把 update 操作体解析为 (old_block, new_block) hunk 列表。

    hunk 内：` ` 为上下文行（保留在两侧），`-` 为删除行（仅 old），
    `+` 为新增行（仅 new）。@@ 头部行忽略。
    """
    hunks: list[tuple[str, str]] = []
    old_lines: list[str] = []
    new_lines: list[str] = []
    for raw in body.splitlines():
        if raw.startswith("@@"):
            hunks.append(("\n".join(old_lines), "\n".join(new_lines)))
            old_lines, new_lines = [], []
            continue
        if not raw:
            continue
        prefix, rest = raw[0], raw[1:]
        if prefix in (" ",):
            old_lines.append(rest)
            new_lines.append(rest)
        elif prefix == "-":
            old_lines.append(rest)
        elif prefix == "+":
            new_lines.append(rest)
    hunks.append(("\n".join(old_lines), "\n".join(new_lines)))
    return hunks


def _patch_apply_hunks(content: str, hunks: list[tuple[str, str]]) -> str:
    """按 hunk 顺序把 old_block 替换为 new_block；找不到则模糊匹配（行内 trim 比较）。"""
    lines = content.splitlines()
    for old_block, new_block in hunks:
        if not old_block.strip():
            # 纯新增：定位到内容末尾（近似语义：追加）
            lines = lines + new_block.splitlines()
            continue
        target = old_block.splitlines()
        idx = -1
        for i in range(len(lines) - len(target) + 1):
            if lines[i:i + len(target)] == target:
                idx = i
                break
        if idx == -1:
            trimmed_target = [t.strip() for t in target]
            for i in range(len(lines) - len(target) + 1):
                if [l.strip() for l in lines[i:i + len(target)]] == trimmed_target:
                    idx = i
                    break
        if idx == -1:
            raise ValueError(f"Patch hunk not found in file:\n{old_block[:200]}")
        lines = lines[:idx] + new_block.splitlines() + lines[idx + len(target):]
    return "\n".join(lines)


def tool_apply_patch(patch_text: str) -> dict:
    """应用 apply_patch 格式的补丁：支持 Add File / Update File / Delete File 操作。

    对齐 apply_patch.txt 语义：
      *** Begin Patch
      *** Add File: <path>
      +<content line>
      *** Update File: <path>
      @@ <anchor>
      -<old line>
      +<new line>
      *** Delete File: <path>
      *** End Patch
    补丁按顺序逐条应用；某个操作失败时已应用的部分保留并明确报告。
    """
    if not patch_text or not patch_text.strip():
        return _env("apply_patch", "Error: patchText is required", error=True)
    sections = _patch_split_sections(patch_text)
    if not sections:
        return _env("apply_patch", "Error: patch rejected: empty patch", error=True)
    applied: list[str] = []
    try:
        for action, rel_path, body in sections:
            target = _resolve(rel_path)
            _ensure_safe(target, "write")
            if action == "add":
                if target.exists():
                    raise ValueError(f"File already exists: {rel_path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_text_raw(target, _patch_add_body(body))
                applied.append(f"A {rel_path}")
            elif action == "delete":
                if not target.exists():
                    raise ValueError(f"File not found: {rel_path}")
                target.unlink()
                applied.append(f"D {rel_path}")
            else:  # update
                if not target.is_file():
                    raise ValueError(f"File not found: {rel_path}")
                text, has_bom = _read_text_raw(target)
                ending = _detect_line_ending(text)
                hunks = _patch_update_hunks(body)
                if not hunks:
                    raise ValueError(f"Update hunk is empty: {rel_path}")
                # 归一化换行后应用，再转换回原文件行尾（对齐 edit.ts 行尾保留）
                normalized = _normalize_line_endings(text)
                normalized = _patch_apply_hunks(normalized, hunks)
                content_new = _convert_line_ending(normalized, ending)
                _write_text_raw(target, content_new, has_bom)
                applied.append(f"M {rel_path}")
            _scan_cache.invalidate(target.parent)
    except NeedsPermission:
        raise
    except Exception as e:
        prefix = (
            f"Error: Unable to apply patch at {rel_path}"
            if not applied
            else f"Error: Patch partially applied before failing at {rel_path}. Applied: {', '.join(applied)}"
        )
        return _env("apply_patch", f"{prefix}\n{e}", error=True, applied=applied)
    return _env("apply_patch", "Applied patch sequentially:\n" + "\n".join(applied), applied=applied)


def tool_grep(pattern: str, include: str = "", context: int = 0, count_only: bool = False, files_only: bool = False, path: str = ".") -> dict:
    """在指定目录的文本文件中按正则表达式搜索内容，支持文件过滤、上下文显示和目录范围。

    与 opencode grep.ts 对齐：输出绝对路径；空结果 "No files found"；
    有结果时头部 "Found N matches"（超限时 "(more matches available)"），按文件分组。
    """
    import re
    context = _coerce_int(context, 0)
    count_only = _coerce_bool(count_only)
    files_only = _coerce_bool(files_only)
    root_path = _resolve(path)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return _env("grep", f"Error: '{path}' is not a directory", error=True)
    use_re = re.compile(pattern, re.MULTILINE | re.DOTALL)
    file_pattern = include if include else "**/*"
    file_matches: list[tuple[Path, list[int]]] = []
    matcher = _gitignore_matcher()
    if matcher is not None:
        glob_rx = glob_to_regex(file_pattern)
        candidates: Iterable[Path] = (
            f
            for dirpath, dirs, files in matcher.walk(root_path)
            for f in files
        )
    else:
        glob_rx = None
        candidates = (f for f in root_path.glob(file_pattern) if f.is_file())
    for f in candidates:
        if not _is_read_allowed(f):
            continue
        if glob_rx is not None:
            try:
                rel = f.relative_to(root_path)
            except ValueError:
                rel = Path(f.name)
            if not glob_rx.match(rel.as_posix()):
                continue
        ext = f.suffix.lower()
        if ext in _MULTIMODAL_EXTS:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found_positions: list[int] = []
        for m in use_re.finditer(text):
            line_start = text[:m.start()].count("\n")
            found_positions.append(line_start)
        if found_positions:
            file_matches.append((f, found_positions))
    if not file_matches:
        return _env("grep", "No files found", matches=0, total_matches=0)
    file_matches.sort(key=lambda t: os.path.getmtime(t[0]), reverse=True)
    output: list[str] = []
    total_matches = 0
    truncated = False
    for f, found_positions in file_matches:
        rel = str(f.resolve())
        if files_only:
            if total_matches >= 100:
                truncated = True
                break
            output.append(rel)
            total_matches += 1
            continue
        if count_only:
            if total_matches >= 100:
                truncated = True
                break
            output.append(f"{rel}: {len(found_positions)} match(es)")
            total_matches += 1
            continue
        seen_lines: set[int] = set()
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except Exception:
            lines = []
        emitted = 0
        file_output: list[str] = []
        for lineno in found_positions:
            if lineno in seen_lines:
                continue
            seen_lines.add(lineno)
            if total_matches + emitted >= 100:
                truncated = True
                break
            emitted += 1
            start = max(0, lineno - context)
            end = min(len(lines), lineno + context + 1)
            for i in range(start, end):
                marker = ">" if i == lineno else " "
                line_text = lines[i].rstrip()
                if len(line_text) > MAX_LINE_LENGTH:
                    line_text = line_text[:MAX_LINE_LENGTH] + "..."
                file_output.append(f"  {marker} Line {i+1}: {line_text}")
            file_output.append("")
        if emitted:
            output.append(f"{rel}:")
            output.extend(file_output)
            total_matches += emitted
        if truncated:
            break
    if truncated:
        output.append(f"... and more. Showing first 100 matches.")
        output.append("(Results are truncated. Consider using a more specific path or pattern.)")
    body = "\n".join(output).rstrip()
    header = f"Found {total_matches} matches (more matches available)" if truncated else f"Found {total_matches} matches"
    return _env(
        "grep",
        f"{header}\n{body}",
        matches=total_matches,
        truncated=truncated,
        pattern=pattern,
    )


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


# ── shell 语义分段校验（opencode bash 语义对齐）─────────────────────────────
# 安全模型不变（白名单/黑名单/SSRF 硬校验），但支持管道/重定向/&&/$(...)/反引号：
# 把命令按 shell 简单命令切段，对【每段】的首命令做白名单校验，每段跑黑名单+SSRF，
# 防止 `cat x | evil` 之类绕过首 token 白名单。

# 分隔符：产生新的简单命令边界
_SHELL_SEP = {"|", "||", "&&", "&", ";", "(", ")"}
# 重定向符：其后一个 token 是重定向目标（文件名/文件描述符），不属于新命令
_REDIRECT_OPS = {">", ">>", "<", "<<", "<&", ">&", "2>", "2>>", "&>", "|&"}


def _first_command(seg: list[str]) -> Optional[str]:
    """取简单命令段的基命令名：跳过环境变量赋值前缀（FOO=bar ...）与命令替换 `$`。"""
    for tok in seg:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            continue
        if tok == "$":
            continue
        return tok
    return None


def _split_shell_segments(command: str) -> list[list[str]]:
    """把 shell 命令切分为简单命令 token 组（引号感知）。

    在 ; | || && & ( ) 处断开；重定向符及其目标附加到当前命令段；
    $(...) 中的子命令因 '(' 断开而自然成为独立段，从而被独立校验。
    """
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


def _backtick_bodies(command: str) -> list[str]:
    """提取命令中反引号命令替换的内部命令文本。"""
    return [m.group(1) for m in _BACKTICK_RE.finditer(command)]


def _check_single_allowed(base_cmd: str, cwd: str | None = None) -> None:
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
    raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")


def _validate_shell_command(command: str, cwd: str | None = None) -> None:
    """tool_execute / 流式执行共用的完整安全校验：白名单 + 黑名单 + SSRF，逐段执行。

    - 反引号命令替换内部命令递归校验
    - 每个简单命令段的首命令过白名单（防 `cat x | evil` 绕过）
    - 每段跑解释器内联黑名单与 SSRF
    """
    for inner in _backtick_bodies(command):
        _validate_shell_command(inner, cwd)
    segments = _split_shell_segments(command)
    if not segments:
        raise ValueError("Empty command")
    for seg in segments:
        base = _first_command(seg)
        if base is None:
            continue
        _check_single_allowed(base, cwd)
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
    - 引号外的 shell 语义（管道/重定向/&&/$VAR/反引号/通配符）→ 是
    - Windows 下基命令为 cmd 内建或 .cmd/.bat/.ps1 垫片（npm 等）→ 是
    - 其余保持安全的 shlex.split + exec 路径
    """
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
            if c == "%" and os.name == "nt":
                # Windows cmd 环境变量展开（%USERPROFILE%）
                return True
        i += 1
    if os.name == "nt":
        return _win_cmd_needs_shell(command)
    return False


def _check_command_allowed(command: str, cwd: str | None = None) -> None:
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
    parts = shlex.split(command)
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
    from app.utils.ssrf import check_url, _host_is_internal

    parts = shlex.split(command)
    if not parts or parts[0].lower() not in _NET_COMMANDS:
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
        if _host_is_internal(candidate):
            raise ValueError(
                f"Command targets internal address '{tok}' which is blocked by SSRF protection"
            )


MAX_EXECUTE_OUTPUT_LENGTH = 30_000


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀掉进程及其整个后代进程树（Windows 用 taskkill /T /F，POSIX 用 killpg）。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _run_shell(command: str, resolved_cwd: str, timeout: int) -> tuple[int, str, str]:
    """通过真实 shell 执行命令（Windows: cmd.exe；POSIX: /bin/sh）。

    进程放入新会话/进程组，超时时杀掉整个进程树（opencode killTree 语义）。
    """
    if os.name == "nt":
        proc = subprocess.Popen(
            command, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=resolved_cwd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen(
            command, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=resolved_cwd, start_new_session=True,
        )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(command, timeout, stdout, stderr)


def _format_execute_output(rc: int, stdout: str, stderr: str, command: str = "") -> dict:
    parts = []
    if stdout:
        parts.append(stdout.rstrip())
    if stderr:
        parts.append(f"[stderr]\n{stderr.rstrip()}")
    output = "\n".join(parts)
    header = f"Exit code: {rc}"
    truncated = len(output) > MAX_EXECUTE_OUTPUT_LENGTH
    preview = output
    output_path = None
    if truncated:
        # 对齐 opencode shell.ts：完整输出写入 truncation 文件，只回传预览 + 提示
        try:
            from app.context.tool_output import _write_truncated
            output_path = _write_truncated(output)
            preview = output[:MAX_EXECUTE_OUTPUT_LENGTH]
            hint = f"\n\n<bash_metadata>\nOutput truncated; full output saved to: {output_path}\n</bash_metadata>"
        except Exception:
            hint = "\n\n<bash_metadata>\nbash tool truncated output as it exceeded 30000 char limit\n</bash_metadata>"
        body = preview + hint
    else:
        body = output
    if body:
        text = f"{header}\n{body}"
    else:
        text = header
    return _env(
        "execute",
        text,
        exit_code=rc,
        truncated=truncated,
        output_path=output_path,
        command=command[:200],
    )


def tool_execute(command: str, timeout: int = 300, workdir: str = ".") -> dict:
    """执行shell命令并返回标准输出、标准错误和退出码。

    支持管道/重定向/&& 等真实 shell 语义；安全校验（白名单/黑名单/SSRF）逐段生效，
    防止 `cat x | evil` 绕过首 token 白名单。无 shell 语义时保持原 exec 路径。
    超长输出（>30KB）截断为预览，完整输出写入 data/truncation/tool_execute_*.txt 并提示路径。
    """
    timeout = _coerce_int(timeout, 300)
    if timeout > 600:
        timeout = 600
    if timeout < 1:
        timeout = 5
    resolved_cwd = _resolve(workdir)
    try:
        _validate_shell_command(command, cwd=resolved_cwd)
    except ValueError as e:
        return _env("execute", f"Error: {e}", error=True)
    mgr = get_perm_mgr()
    decision = mgr.check(str(resolved_cwd), "execute")
    if decision == "ask":
        raise NeedsPermission(str(resolved_cwd), "execute", "tool_execute", {"command": command, "timeout": timeout, "workdir": workdir})
    if decision == "deny":
        return _env("execute", f"Error: access denied to directory '{workdir}'", error=True)
    try:
        if _needs_shell(command):
            rc, stdout, stderr = _run_shell(command, str(resolved_cwd), timeout)
            return _format_execute_output(rc, stdout, stderr, command)
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
        return _format_execute_output(result.returncode, result.stdout, result.stderr, command)
    except subprocess.TimeoutExpired:
        return _env("execute", f"Error: command timed out after {timeout}s", error=True, timed_out=True)
    except ValueError as e:
        return _env("execute", f"Error: {e}", error=True)
    except Exception as e:
        detail = str(e) or repr(e) or type(e).__name__
        return _env("execute", f"Error executing command: {detail}", error=True)
