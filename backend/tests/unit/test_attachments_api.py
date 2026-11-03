"""L1：聊天附件正文提取接口。"""

import base64

import pytest
from fastapi import HTTPException

from app.api.attachments import ExtractRequest, extract_attachment


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


async def test_extract_returns_full_text():
    out = await extract_attachment(
        ExtractRequest(
            filename="a.txt", mime_type="text/plain", data=_b64("9.10 接口说明")
        )
    )
    assert out["text"] == "9.10 接口说明"
    assert out["truncated"] is False
    assert out["chars"] == len("9.10 接口说明")


async def test_extract_truncates_by_setting(monkeypatch):
    from app import attachment_processor

    monkeypatch.setattr(attachment_processor, "extract_max_chars", lambda: 3)
    out = await extract_attachment(
        ExtractRequest(filename="a.txt", mime_type="text/plain", data=_b64("123456"))
    )
    assert out["text"] == "123"
    assert out["truncated"] is True


async def test_extract_rejects_unsupported():
    with pytest.raises(HTTPException) as exc:
        await extract_attachment(
            ExtractRequest(
                filename="a.bin",
                mime_type="application/octet-stream",
                data="QUJD",
            )
        )
    assert exc.value.status_code == 422
