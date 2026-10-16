"""拆分模块 `common`（含 DEFAULT_READ_LIMIT、MAX_BYTES、MAX_BYTES_LABEL、MAX_LINE_LENGTH、MAX_LINE_SUFFIX、SAMPLE_BYTES、_AUDIO_EXTS、_BINARY_EXTS、_DOC_EXTS、_IMAGE_EXTS、_MIME_MAP、_MULTIMODAL_EXTS、_PDF_EXTS、_TEXT_EXTS、_VIDEO_EXTS、_coerce_bool、_coerce_int、_env、unwrap）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──
















# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

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


def first_missing_ancestor(path):
    """返回路径上**最高**的那个不存在的祖先；整条链都存在则返回 None。

    `Path.is_dir()` 对「不存在」和「是文件」都返回 False —— 直接拿它判错会把 ENOENT
    误报成 ENOTDIR（实测 `tool_ls D:\\AgentSuper` 在路径根本不存在时回
    "is not a directory"，把模型带进「D:\\AgentSuper 是不是个文件 / 路径格式有问题」
    的死循环，反复 ls D:\\ 而从不创建目录）。先定位缺失的那一段，报错才说得清。

    自底向上穿过**连续**的不存在段，返回最高的那一段：`a/不存在/b/c` → `a/不存在`。
    它的 parent 必然存在，可用作相似名建议的扫描基准。
    """
    from pathlib import Path as _P
    cur = _P(path)
    missing = None
    while True:
        try:
            if not cur.exists():
                missing = cur
            else:
                break  # 触到第一个存在的祖先，缺失段到此为止
        except OSError:
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return missing


def dir_not_found_message(path_str: str, target) -> str:
    """目录不存在时的报错文案：区分「不存在」与「是文件」，并给出可执行的下一步。"""
    from pathlib import Path as _P
    missing = first_missing_ancestor(target)
    lines = [f"Directory not found: {path_str}"]
    if missing is not None and _P(missing) != _P(target):
        lines.append(f"Missing path segment: {missing}")
    anchor = _P(missing if missing is not None else target).parent
    base = _P(target).name.lower()
    entries: list[str] = []
    try:
        from .workspace import _is_read_allowed
        if _is_read_allowed(anchor):  # 不在报错里泄露未授权目录的条目
            entries = sorted(e.name for e in anchor.iterdir())
    except Exception:
        entries = []
    if not base:
        candidates: list[str] = []
    elif len(base) >= 3:
        candidates = [e for e in entries if base in e.lower() or e.lower() in base]
    else:
        # 1~2 字符的名字（如 "x"）子串匹配会命中整个目录，只用前缀匹配避免噪声建议
        candidates = [e for e in entries if e.lower().startswith(base)]
    suggestions = [str(anchor / e) for e in candidates][:3]
    if suggestions:
        lines.append("")
        lines.append("Did you mean one of these?")
        lines.extend(suggestions)
    lines.append("")
    lines.append(
        "The path does not exist. tool_write_file creates missing parent directories "
        "automatically, or run mkdir via tool_execute; tool_ls an existing parent to look around."
    )
    return "\n".join(lines)


def not_a_directory_message(path_str: str) -> str:
    """路径存在但确实是文件时的报错文案（与「不存在」严格区分）。"""
    return (
        f"Error: '{path_str}' is not a directory (it is an existing file). "
        "Use tool_read_file to read it, or tool_ls its parent directory."
    )


__all__ = ["DEFAULT_READ_LIMIT", "MAX_BYTES", "MAX_BYTES_LABEL", "MAX_LINE_LENGTH", "MAX_LINE_SUFFIX", "SAMPLE_BYTES", "_AUDIO_EXTS", "_BINARY_EXTS", "_DOC_EXTS", "_IMAGE_EXTS", "_MIME_MAP", "_MULTIMODAL_EXTS", "_PDF_EXTS", "_TEXT_EXTS", "_VIDEO_EXTS", "_coerce_bool", "_coerce_int", "_env", "dir_not_found_message", "first_missing_ancestor", "not_a_directory_message", "unwrap"]
