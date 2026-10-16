"""L1 单元测试：聊天附件图片回显（agent.md §10.2 / F8·D2）。"""

import base64
import io

from langchain_core.messages import HumanMessage

from app.deps import (
    PlatformContext,
    _files_payload,
    _image_blocks,
    _msg_files,
    _msg_role,
    initial_state,
)
from app.image_processor import enrich_image_files, is_image_file, make_thumbnail


def _png_b64(color=(30, 80, 220), size=(48, 48)) -> str:
    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_files_payload_keeps_echo_keys_only():
    payload = _files_payload(
        [
            {"filename": "a.png", "mime_type": "image/png", "data": "AAA", "_thumb": "TTT", "width": 100},
            {"filename": ""},
            "not-a-dict",
        ]
    )
    assert payload == [{"filename": "a.png", "mime_type": "image/png", "data": "AAA", "_thumb": "TTT"}]


def test_image_blocks_inline_data_url():
    blocks = _image_blocks("问题", [{"mime_type": "image/png", "data": "QUJD"}])
    assert blocks[0] == {"type": "text", "text": "问题"}
    assert blocks[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert _image_blocks("问题", [{"mime_type": "image/png", "data": ""}]) == [
        {"type": "text", "text": "问题"}
    ]


def test_initial_state_stores_files_for_history_echo():
    files = [{"filename": "a.png", "mime_type": "image/png", "data": "QUJD", "_thumb": "Tg"}]
    state = initial_state("看这张图", "u1", files=files, model=None)
    msg = state["messages"][0]
    assert isinstance(msg, HumanMessage)
    assert msg.additional_kwargs["files"] == files
    assert "[附件：a.png]" in msg.content
    assert initial_state("无附件", "u1")["messages"][0].additional_kwargs == {}


def test_is_image_file_by_mime_or_extension():
    assert is_image_file({"filename": "x.png"})
    assert is_image_file({"filename": "x.bin", "mime_type": "image/jpeg"})
    assert not is_image_file({"filename": "x.pdf", "mime_type": "application/pdf"})
    assert not is_image_file("nope")


def test_make_thumbnail_returns_jpeg_base64():
    src = _png_b64()
    thumb = make_thumbnail(src, 32)
    assert thumb and thumb != src
    assert base64.b64decode(thumb)[:2] == b"\xff\xd8"


async def test_enrich_image_files_adds_thumb_only_for_images():
    files = await enrich_image_files(
        [
            {"filename": "a.png", "mime_type": "image/png", "data": _png_b64()},
            {"filename": "doc.pdf", "mime_type": "application/pdf", "data": "QUJD"},
        ]
    )
    assert "_thumb" in files[0]
    assert files[1] == {"filename": "doc.pdf", "mime_type": "application/pdf", "data": "QUJD"}
    assert await enrich_image_files(None) == []


class _FakeCtx:
    sessions: dict = {}

    def __init__(self, raw):
        self.raw = raw

    def message_id(self, session_id: str, index: int) -> str:
        return f"{session_id}_m{index}"

    async def _raw_messages(self, session_id: str):
        return self.raw


async def test_session_messages_projects_files_and_text():
    files = [{"filename": "a.png", "mime_type": "image/png", "data": "QUJD", "_thumb": "Tg"}]
    raw = [
        HumanMessage(
            content=[{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}],
            additional_kwargs={"files": files},
        ),
        HumanMessage(content="无附件消息"),
    ]
    out = await PlatformContext.session_messages(_FakeCtx(raw), "ses_x")
    assert [m["type"] for m in out] == ["user", "user"]
    assert out[0]["content"] == "看图\n[图片]"
    assert out[0]["data"]["files"] == files
    assert out[1]["content"] == "无附件消息"
    assert "files" not in out[1]["data"]


def test_msg_helpers_read_raw_messages():
    msg = HumanMessage(content="hi", additional_kwargs={"files": [{"filename": "a.png"}]})
    assert _msg_role(msg) == "human"
    assert _msg_files(msg) == [{"filename": "a.png"}]
    assert _msg_role({"role": "ai"}) == "ai"
    assert _msg_files({"content": "x"}) == []
