"""聊天附件正文提取（「查看正文」弹层）。

前端把消息里已有的附件 `{filename, mime_type, data(base64)}` 回传，后端现场
提取文本并返回；不依赖会话临时目录是否仍存在。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import attachment_processor

router = APIRouter()


class ExtractRequest(BaseModel):
    filename: str = ""
    mime_type: str = ""
    data: str = Field(min_length=1)


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
