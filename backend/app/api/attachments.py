"""聊天附件正文提取（「查看正文」弹层）。

前端把消息里已有的附件 `{filename, mime_type, data(base64)}` 回传，后端现场
提取文本并返回；不依赖会话临时目录是否仍存在。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import attachment_processor
from app.deps import default_session_directory, get_context
from execution.uri import parse_file_uri, slice_lines

router = APIRouter()


class ExtractRequest(BaseModel):
    filename: str = ""
    mime_type: str = ""
    data: str = Field(min_length=1)


class ReadRequest(BaseModel):
    uri: str
    name: str = ""
    description: str = ""


def _workspace_root(request: Request, session_id: str) -> Path:
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    ctx = get_context(request)
    sess = ctx.sessions.get(session_id)
    directory = getattr(sess, "directory", None) if sess is not None else None
    return Path(directory or default_session_directory(session_id)).resolve()


def _resolve_uri(root: Path, uri: str) -> tuple[Path, int, int]:
    try:
        parsed = parse_file_uri(uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if parsed is None:
        target = (root / uri).resolve()
        return target, 0, 0
    target = parsed.path
    if not Path(target).is_absolute():
        target = str((root / target).resolve())
    resolved = Path(target).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="uri escapes the session workspace") from exc
    return resolved, parsed.start, parsed.end


@router.post("/read")
async def read_attachment(request: Request, session_id: str = "") -> dict:
    """Read a workspace attachment by ``file://`` or relative URI."""
    body = ReadRequest(**await request.json())
    root = _workspace_root(request, session_id)
    target, start, end = _resolve_uri(root, body.uri)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {body.uri}")
    text = target.read_text(encoding="utf-8", errors="replace")
    if start:
        text = slice_lines(text, start, end)
    limit = attachment_processor.extract_max_chars()
    return {
        "uri": body.uri,
        "name": body.name or target.name,
        "description": body.description,
        "text": text[:limit],
        "truncated": len(text) > limit,
        "chars": min(len(text), limit),
    }


@router.get("/list")
async def list_attachments(request: Request, session_id: str = "", path: str = "attachments") -> dict:
    """One-level directory listing under the session workspace."""
    root = _workspace_root(request, session_id)
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="path escapes the session workspace") from exc
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"directory not found: {path}")
    entries = sorted(target.iterdir(), key=lambda item: item.name)
    return {
        "data": [
            {
                "uri": item.as_uri(),
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            }
            for item in entries[:500]
        ]
    }


@router.post("/extract")
async def extract_attachment(body: ExtractRequest) -> dict:
    text = attachment_processor.extract_attachment_text(
        body.filename, body.mime_type, body.data
    )
    if text is None:
        raise HTTPException(
            status_code=422, detail="无法解析该附件内容（格式不支持或文件损坏）"
        )
    if not text.strip():
        raise HTTPException(
            status_code=422, detail="未提取到文本内容（可能是扫描件或空文件）"
        )
    limit = attachment_processor.extract_max_chars()
    truncated = len(text) > limit
    return {
        "filename": body.filename,
        "text": text[:limit],
        "truncated": truncated,
        "chars": min(len(text), limit),
    }
