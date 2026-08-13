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

from app.filesystem import ScanCache, get_project
from app.permission import get_manager as get_perm_mgr, NeedsPermission, current_session_workspace

# 回退基准：项目上下文未初始化时使用 backend/（历史行为）
_WORKSPACE_FALLBACK = Path(__file__).resolve().parents[2]

_scan_cache = ScanCache()


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


def tool_ls(path: str = ".") -> str:
    """列出指定目录下的文件和子目录，显示类型、大小和修改时间。"""
    target = _resolve(path)
    _ensure_safe(target, "read")
    if not target.is_dir():
        return f"Error: '{path}' is not a directory"
    rows = []
    for node in _scan_cache.list_dir(target):
        entry = Path(node.path)
        try:
            st = entry.stat()
            size = st.st_size
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            kind = "d" if node.type == "dir" else "f"
            rows.append(f"{kind} {size:>10}  {mtime}  {node.name}")
        except OSError:
            rows.append(f"? {'':>10}  {'':19}  {node.name}")
    return "\n".join(rows) if rows else "(empty)"


def _is_binary(path: Path) -> bool:
    """检测文件是否为二进制（opencode read.ts 同款逻辑：扩展名黑名单 + NUL 字节 + 非打印字符占比）。"""
    ext = path.suffix.lower()
    if ext in _BINARY_EXTS:
        return True
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    if not raw:
        return False
    sample = raw[:4096]
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


def tool_read_file(path: str, offset: int = 1, limit: int = 0) -> str:
    """读取文件内容，文本文件按 cat -n 格式返回并支持行偏移/限制，图片/PDF/音视频返回base64。"""
    target = _resolve(path)
    _ensure_safe(target, "read")
    offset = _coerce_int(offset, 1)
    limit = _coerce_int(limit, 0)
    if not target.is_file():
        return _file_not_found_suggestion(path, target)
    ext = target.suffix.lower()
    if ext in _MULTIMODAL_EXTS:
        try:
            raw = target.read_bytes()
            b64 = base64.b64encode(raw).decode("utf-8")
            mime = _MIME_MAP.get(ext, "application/octet-stream")
            return f"data:{mime};base64,{b64}"
        except Exception as e:
            return f"Error reading file: {e}"
    if ext not in _TEXT_EXTS and _is_binary(target):
        return f"Cannot read binary file: {path}"
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
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
    content_lines = []
    for i, line in enumerate(selected):
        line_no = start + i + 1
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "..."
        content_lines.append(f"{line_no:05d}| {line}")
    content = "\n".join(content_lines)
    last_read = start + len(selected)
    if total > last_read:
        footer = f"\n\n(File has more lines. Use 'offset' parameter to read beyond line {last_read})"
    else:
        footer = f"\n\n(End of file - total {total} lines)"
    return f"<file>\n{content}{footer}\n</file>"


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
        _scan_cache.invalidate(target.parent)
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
        _scan_cache.invalidate(target.parent)
        return f"{action} {path} ({total} bytes total)"
    except Exception as e:
        return f"Error appending to file: {e}"


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


def tool_edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """在文件中查找并替换指定字符串。支持模糊匹配；oldString 匹配到多处时报错（除非 replace_all=True）。"""
    target = _resolve(path)
    _ensure_safe(target, "write")
    replace_all = _coerce_bool(replace_all)
    if not target.is_file():
        return f"File {path} not found"
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"
    try:
        if old_string == "":
            content_new = new_string
        else:
            content_new = _edit_replace(text, old_string, new_string, replace_all)
    except ValueError as e:
        return f"Error: {e}"
    try:
        target.write_text(content_new, encoding="utf-8")
        _scan_cache.invalidate(target.parent)
        return f"Edited {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def tool_glob(pattern: str, root: str = ".") -> str:
    """在指定目录中按glob模式搜索文件，返回匹配路径列表（默认工作区为相对路径，自定义 root 为绝对路径）。"""
    root_path = _resolve(root)
    _ensure_safe(root_path, "read")
    if not root_path.is_dir():
        return f"Error: '{root}' is not a directory"
    matches = [m for m in root_path.glob(pattern) if _is_read_allowed(m)]
    if not matches:
        return f"No files found for: {pattern}"
    matches.sort(key=lambda m: os.path.getmtime(m), reverse=True)
    if root_path == _workspace():
        lines = [str(m.relative_to(_workspace())) for m in matches[:100]]
    else:
        lines = [str(m.resolve()) for m in matches[:100]]
    if len(matches) > 100:
        lines.append("... and {} more".format(len(matches) - 100))
        lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
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
            _scan_cache.invalidate(target.parent)
            return f"Deleted directory {path}"
        except OSError as e:
            return f"Error deleting directory: {e}"
    try:
        target.unlink()
        _scan_cache.invalidate(target.parent)
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
        _scan_cache.invalidate(src.parent)
        _scan_cache.invalidate(dst.parent)
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
    file_matches: list[tuple[Path, list[int]]] = []
    for f in root_path.glob(file_pattern):
        if not f.is_file():
            continue
        if not _is_read_allowed(f):
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
        return f"No files found for: {pattern}"
    file_matches.sort(key=lambda t: os.path.getmtime(t[0]), reverse=True)
    output: list[str] = []
    total_matches = 0
    truncated = False
    for f, found_positions in file_matches:
        if root_path == _workspace():
            rel = str(f.relative_to(_workspace()))
        else:
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
        output.append(f"--- {rel} ---")
        seen_lines: set[int] = set()
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except Exception:
            lines = []
        emitted = 0
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
                output.append(f"{marker} {i+1:>6}: {line_text}")
            output.append("")
        total_matches += emitted
        if truncated:
            break
    if truncated:
        output.append(f"... and more. Showing first 100 matches.")
        output.append("(Results are truncated. Consider using a more specific search pattern.)")
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


def _check_command_allowed(command: str, cwd: str | None = None) -> None:
    """Check the base command against the whitelist. Raises ValueError if not allowed.

    白名单校验首个 token；若首 token 含路径分隔符（如 `.venv/Scripts/python.exe`），
    则放行能解析到工作区（会话目录或 backend/ 根）内真实文件的命令。
    """
    parts = shlex.split(command)
    base_cmd = parts[0].lower() if parts else ""
    if not base_cmd:
        raise ValueError("Empty command")
    if base_cmd in _ALLOWED_COMMANDS:
        return
    if "/" in base_cmd or "\\" in base_cmd:
        bases = [Path(cwd)] if cwd else []
        session = current_session_workspace()
        if session:
            bases.append(Path(session))
        bases.append(_workspace())
        for base in bases:
            candidate = (base / parts[0]).resolve()
            if candidate.is_file() and candidate.is_relative_to(base):
                return
        raise ValueError(
            f"Command '{base_cmd}' is not in the allowed whitelist (path must point to an existing file inside the workspace)"
        )
    raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")


# 解释器类命令的 -c/-e/-Command 参数中禁止出现的高危模式
_DANGEROUS_PATTERNS = (
    # "os.system", "os.popen", "subprocess", "os.exec", "eval(", "exec(",
    # "__import__", "importlib", "pickle", "marshal",
    # "socket.", "urllib", "requests.", "http.client", "aiohttp", "httpx",
    # "base64", "ctypes", "win32api", "winreg", "b64decode",
    # "Invoke-Expression", "IEX", "Invoke-WebRequest", "IWR", "DownloadString",
    # "DownloadFile", "WebClient", "Net.WebClient", "Start-Process",
    # "Add-MscProject", "shutdown", "reg add", "net user", "net localgroup",
    # "whoami /all", "netsh", "taskkill", "format ", "del /f",
    # "/dev/tcp", "/dev/udp", "curl", "wget",
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
    i = 1
    while i < len(parts):
        if parts[i].lower() == interpreter_flag:
            inline = " ".join(parts[i + 1:])
            lowered = inline.lower()
            for pat in _DANGEROUS_PATTERNS:
                if pat in lowered:
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


def tool_execute(command: str, timeout: int = 300, work_dir: str = ".") -> str:
    """执行shell命令并返回标准输出、标准错误和退出码。"""
    timeout = _coerce_int(timeout, 300)
    if timeout > 600:
        timeout = 600
    if timeout < 1:
        timeout = 5
    resolved_cwd = _resolve(work_dir)
    _check_command_allowed(command, cwd=resolved_cwd)
    _check_command_blacklist(command)
    _ssrf_check_command(command)
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
        if len(output) > MAX_EXECUTE_OUTPUT_LENGTH:
            output = output[:MAX_EXECUTE_OUTPUT_LENGTH]
            output += "\n\n<bash_metadata>\nbash tool truncated output as it exceeded 30000 char limit\n</bash_metadata>"
        if output:
            return f"{header}\n{output}"
        return header
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error executing command: {e}"
