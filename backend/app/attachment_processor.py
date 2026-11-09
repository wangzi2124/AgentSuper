"""聊天附件处理：文本提取、工作区落盘与内联装配。

聊天输入框的附件只带 base64 内容。为了让模型能读长文档（例如 API 文档的
9.10 小节），附件统一落盘到会话工作区 `attachments/`，模型可用 `read_file`
读取全文；同时按配置上限内联一段正文，兼顾无工具模型。
"""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

MAX_RAW_BYTES = 20 * 1024 * 1024
DEFAULT_INLINE_MAX_CHARS_PER_FILE = 6_000
DEFAULT_INLINE_MAX_CHARS_TOTAL = 12_000
DEFAULT_EXTRACT_MAX_CHARS = 200_000
# 单个可读文件超过该长度时切成多个分片，避免 read_file 的中间截断丢掉 9.10 这类段落。
SINGLE_READ_MAX_CHARS = 12_000
READ_CHUNK_CHARS = 6_000

_ATTACHMENTS_DIR = "attachments"
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".log",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".ini",
        ".cfg",
        ".conf",
        ".toml",
        ".sql",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".cs",
        ".php",
        ".rb",
        ".sh",
        ".ps1",
    }
)


def _setting_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        from config import get_settings

        value = int(getattr(get_settings(), name, default) or default)
    except Exception:  # noqa: BLE001 - 配置不可用时用默认值
        value = default
    return max(minimum, value)


def inline_max_chars_per_file() -> int:
    return _setting_int(
        "attachment_inline_max_chars_per_file", DEFAULT_INLINE_MAX_CHARS_PER_FILE
    )


def inline_max_chars_total() -> int:
    return _setting_int(
        "attachment_inline_max_chars_total", DEFAULT_INLINE_MAX_CHARS_TOTAL
    )


def extract_max_chars() -> int:
    return _setting_int("attachment_extract_max_chars", DEFAULT_EXTRACT_MAX_CHARS)


def _decode_base64(data: str) -> bytes:
    payload = data
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload, validate=False)


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _pdf_text(raw: bytes) -> str:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(raw))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(raw: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(raw))
    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _xlsx_text(raw: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    lines: list[str] = []
    for sheet in wb.worksheets:
        lines.append(f"[工作表：{sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _pptx_text(raw: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(raw))
    lines: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        lines.append(f"[第 {i} 页]")
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                lines.append(text)
    return "\n".join(lines)


def extract_attachment_text(
    filename: str, mime_type: str, data_b64: str
) -> str | None:
    """提取附件文本；不支持的格式或解析失败返回 None。"""
    if not data_b64:
        return None
    try:
        raw = _decode_base64(data_b64)
    except Exception:  # noqa: BLE001 - 非法 base64 按解析失败处理
        return None
    if not raw or len(raw) > MAX_RAW_BYTES:
        return None

    mime = (mime_type or "").lower()
    ext = Path(filename or "").suffix.lower()
    if not ext:
        ext = (mimetypes.guess_extension(mime) or "").lower()

    try:
        if mime.startswith("text/") or ext in _TEXT_EXTENSIONS:
            return _decode_text(raw)
        if ext == ".pdf" or mime == "application/pdf":
            return _pdf_text(raw)
        if ext == ".docx" or "wordprocessingml" in mime:
            return _docx_text(raw)
        if ext in {".xlsx", ".xlsm"} or "spreadsheetml" in mime:
            return _xlsx_text(raw)
        if ext == ".pptx" or "presentationml" in mime:
            return _pptx_text(raw)
    except Exception as exc:  # noqa: BLE001 - 文档损坏/缺库时不阻断发送
        logger.warning("attachment text extraction failed (%s): %s", filename, exc)
        return None
    return None


def is_text_attachment(filename: str, mime_type: str) -> bool:
    mime = (mime_type or "").lower()
    ext = Path(filename or "").suffix.lower()
    if not ext:
        ext = (mimetypes.guess_extension(mime) or "").lower()
    return mime.startswith("text/") or ext in _TEXT_EXTENSIONS


@dataclass(frozen=True)
class StagedAttachment:
    """落盘结果：原文件、可读路径（大文档拆成多个分片）、提取正文。"""

    original_path: str | None = None
    read_paths: tuple[str, ...] = ()
    text: str | None = None


def safe_attachment_filename(filename: str, *, fallback: str) -> str:
    """去掉路径与 Windows 非法字符，保留中文/扩展名，限制长度。"""
    name = Path(str(filename or "")).name.strip()
    name = _UNSAFE_NAME.sub("_", name).strip(" .")
    if not name:
        name = fallback
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) <= 10:
        stem = stem[:80] or fallback
        return f"{stem}.{ext}"
    return name[:80] or fallback


def _write_readable_text(target: Path, text: str, *, binary: bool) -> tuple[str, ...]:
    """写出模型可读的文本：小文件单份，大文件按块拆分。"""
    if not text.strip():
        return ()
    base = target.name
    if len(text) <= SINGLE_READ_MAX_CHARS:
        if not binary:
            return (f"{_ATTACHMENTS_DIR}/{base}",)
        side = target.with_name(base + ".extracted.txt")
        try:
            side.write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot stage extracted text %s: %s", side, exc)
            return ()
        return (f"{_ATTACHMENTS_DIR}/{side.name}",)

    parts: list[str] = []
    for i in range(0, len(text), READ_CHUNK_CHARS):
        part = target.with_name(f"{base}.part{i // READ_CHUNK_CHARS + 1:02d}.txt")
        try:
            part.write_text(text[i : i + READ_CHUNK_CHARS], encoding="utf-8")
        except OSError as exc:
            logger.warning("cannot stage attachment part %s: %s", part, exc)
            continue
        parts.append(f"{_ATTACHMENTS_DIR}/{part.name}")
    return tuple(parts)


def stage_attachments(
    files: list | None, workspace_dir: str | None
) -> list[StagedAttachment]:
    """把所有附件写入 `<workspace>/attachments/`，返回与 files 对齐的落盘结果。"""
    items = files or []
    if not workspace_dir:
        return [StagedAttachment() for _ in items]
    base = Path(workspace_dir) / _ATTACHMENTS_DIR
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("cannot create attachment dir %s: %s", base, exc)
        return [StagedAttachment() for _ in items]

    staged: list[StagedAttachment] = []
    for index, f in enumerate(items, start=1):
        if not isinstance(f, dict) or not f.get("data"):
            staged.append(StagedAttachment())
            continue
        original = str(f.get("filename") or f"file_{index}")
        mime = str(f.get("mime_type") or "")
        data = str(f.get("data") or "")
        ext = (mimetypes.guess_extension(mime) or "").lower()
        fallback = f"file_{index}{ext}"
        name = safe_attachment_filename(original, fallback=fallback)
        target = base / f"{index}_{name}"
        if target.exists():
            stem, dot, suf = name.rpartition(".")
            unique = (
                f"{stem}_{uuid4().hex[:6]}{dot}{suf}"
                if dot
                else f"{name}_{uuid4().hex[:6]}"
            )
            target = base / f"{index}_{unique}"

        original_rel: str | None = None
        try:
            target.write_bytes(_decode_base64(data))
            original_rel = f"{_ATTACHMENTS_DIR}/{target.name}"
        except Exception as exc:  # noqa: BLE001 - 落盘失败不影响本轮发送
            logger.warning("cannot stage attachment %s: %s", original, exc)

        text = extract_attachment_text(original, mime, data)
        binary = not is_text_attachment(original, mime)
        read_paths = (
            _write_readable_text(target, text, binary=binary)
            if text and text.strip()
            else ()
        )
        staged.append(
            StagedAttachment(
                original_path=original_rel,
                read_paths=read_paths,
                text=text,
            )
        )
    return staged


def build_attachment_sections(
    files: list[dict], staged: list[StagedAttachment | None] | None = None
) -> str:
    """把非图片附件正文与可读路径拼成模型侧上下文片段。"""
    remaining = inline_max_chars_total()
    per_file = inline_max_chars_per_file()
    sections: list[str] = []
    for index, f in enumerate(files):
        name = str(f.get("filename") or "附件")
        stage = staged[index] if staged and index < len(staged) else None
        read_paths = stage.read_paths if stage else ()
        if len(read_paths) == 1:
            path_hint = (
                f"\n可读文件：{read_paths[0]}\n"
                "如需完整内容，请用 read_file / grep 读取上述路径（不要臆造未读到的内容）。"
            )
        elif read_paths:
            shown = list(read_paths[:40])
            if len(read_paths) > len(shown):
                shown.append(f"...（共 {len(read_paths)} 个分片）")
            listing = "\n".join(f"- {p}" for p in shown)
            path_hint = (
                "\n可读分片（先用 grep 定位目标，再 read_file 对应分片）：\n"
                f"{listing}"
            )
        elif stage and stage.original_path:
            path_hint = (
                f"\n原文件：{stage.original_path}\n"
                "该格式无法提取文本，只能用 ls 确认存在，不要臆造内容。"
            )
        else:
            path_hint = ""
        has_read = bool(read_paths)
        text = stage.text if stage else None
        if text is None:
            text = extract_attachment_text(
                name, str(f.get("mime_type") or ""), str(f.get("data") or "")
            )
        if text is None:
            sections.append(
                f"\n\n【附件：{name}】{path_hint}\n（无法解析该文件内容：格式不支持或文件损坏）"
            )
            continue
        if not text.strip():
            sections.append(f"\n\n【附件：{name}】{path_hint}\n（文件为空或未提取到文本）")
            continue
        limit = min(per_file, remaining)
        body = text[:limit] if limit > 0 else ""
        remaining -= len(body)
        if body:
            if len(text) > len(body):
                suffix = (
                    "\n…[内联正文已截断，完整内容见上述文件]"
                    if has_read
                    else "\n…[附件内容已截断]"
                )
            else:
                suffix = ""
            sections.append(f"\n\n【附件：{name}】{path_hint}\n{body}{suffix}")
        else:
            sections.append(
                f"\n\n【附件：{name}】{path_hint}\n（内联预算已用完，请用 read_file 读取完整内容）"
            )
    return "".join(sections)
