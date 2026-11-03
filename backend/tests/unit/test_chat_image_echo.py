"""L1 单元测试：聊天附件图片回显（agent.md §10.2 / F8·D2）。"""

import base64
import io

from langchain_core.messages import HumanMessage

from app.attachment_processor import (
    extract_attachment_text,
    safe_attachment_filename,
    stage_attachments,
)
from app.deps import (
    PlatformContext,
    _attachment_content,
    _document_context,
    _files_payload,
    _image_blocks,
    _msg_files,
    _msg_role,
    initial_state,
)
from app.image_processor import enrich_image_files, is_image_file, make_thumbnail


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


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
    state = initial_state("看这张图", "u1", files=files, model="unknown/no-vision")
    msg = state["messages"][0]
    assert isinstance(msg, HumanMessage)
    assert msg.additional_kwargs["files"] == files
    assert "[附件：a.png]" in msg.content
    assert initial_state("无附件", "u1")["messages"][0].additional_kwargs == {}


def test_initial_state_uses_default_model_for_vision(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "llm_model", "deepseek/deepseek-v4-flash")
    monkeypatch.setattr("app.deps._vision_capable", lambda _model: True)
    files = [{"filename": "a.png", "mime_type": "image/png", "data": _png_b64()}]
    state = initial_state("看这张图", "u1", files=files, model=None)
    content = state["messages"][0].content
    assert isinstance(content, list)
    assert any(part.get("type") == "image_url" for part in content)


def test_non_vision_model_uses_image_caption():
    files = [
        {
            "filename": "a.png",
            "mime_type": "image/png",
            "data": "QUJD",
            "_caption": "一张接口文档截图，包含 9.10 小节",
        }
    ]
    content = _attachment_content("看图", files, "unknown/no-vision")
    assert isinstance(content, str)
    assert "图片内容描述" in content
    assert "9.10 小节" in content


def test_non_image_attachments_not_in_visible_content():
    files = [
        {"filename": "a.txt", "mime_type": "text/plain", "data": _b64("苹果 10 元")},
        {"filename": "b.md", "mime_type": "text/markdown", "data": _b64("香蕉 20 元")},
    ]
    content = _attachment_content("对比这两个文件", files, None)
    assert isinstance(content, str)
    assert "[附件：a.txt、b.md]" in content
    assert "苹果 10 元" not in content
    assert "香蕉 20 元" not in content


def test_document_context_contains_body_and_path_hint(tmp_path):
    files = [
        {"filename": "a.txt", "mime_type": "text/plain", "data": _b64("苹果 10 元")},
        {"filename": "b.md", "mime_type": "text/markdown", "data": _b64("香蕉 20 元")},
    ]
    staged = stage_attachments(files, str(tmp_path))
    context = _document_context(files, staged)
    assert "苹果 10 元" in context
    assert "香蕉 20 元" in context
    assert "attachments/1_a.txt" in context
    assert "attachments/2_b.md" in context
    assert (tmp_path / "attachments" / "1_a.txt").read_text("utf-8") == "苹果 10 元"


def test_initial_state_exposes_document_context_for_execute(tmp_path):
    files = [
        {"filename": "a.txt", "mime_type": "text/plain", "data": _b64("苹果 10 元")},
        {"filename": "b.txt", "mime_type": "text/plain", "data": _b64("香蕉 20 元")},
    ]
    staged = stage_attachments(files, str(tmp_path))
    state = initial_state(
        "对比这两个文件", "u1", files=files, staged_attachments=staged
    )
    assert "苹果 10 元" in state["attachment_context"]
    assert "香蕉 20 元" in state["attachment_context"]
    assert "attachments/1_a.txt" in state["attachment_context"]
    # 可见 content 只保留附件名，不内联正文
    assert "苹果 10 元" not in state["messages"][0].content


def test_unsupported_attachment_reports_unparseable():
    files = [{"filename": "x.bin", "mime_type": "application/octet-stream", "data": "QUJD"}]
    context = _document_context(files)
    assert "无法解析该文件内容" in context


def test_stage_attachments_sanitizes_names(tmp_path):
    files = [{"filename": "坏_名称?.pdf", "mime_type": "application/pdf", "data": "QUJD"}]
    staged = stage_attachments(files, str(tmp_path))
    path = staged[0].original_path or ""
    assert path.startswith("attachments/1_")
    assert "坏" in path and "名称" in path
    assert "?" not in path
    assert safe_attachment_filename("a" * 200 + ".txt", fallback="f").endswith(".txt")


def test_binary_attachment_stages_extracted_text(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("9.10 接口说明")
    buf = io.BytesIO()
    doc.save(buf)
    files = [
        {
            "filename": "spec.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "data": base64.b64encode(buf.getvalue()).decode("ascii"),
        }
    ]
    staged = stage_attachments(files, str(tmp_path))
    read_path = staged[0].read_paths[0] if staged[0].read_paths else ""
    assert read_path.endswith(".docx.extracted.txt")
    assert "9.10 接口说明" in (tmp_path / read_path).read_text("utf-8")
    context = _document_context(files, staged)
    assert read_path in context
    assert "9.10 接口说明" in context


def test_large_binary_attachment_is_chunked(tmp_path):
    from docx import Document

    doc = Document()
    for i in range(2_000):
        doc.add_paragraph(f"9.10 接口说明 {i} " + "x" * 40)
    buf = io.BytesIO()
    doc.save(buf)
    files = [
        {
            "filename": "big.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "data": base64.b64encode(buf.getvalue()).decode("ascii"),
        }
    ]
    staged = stage_attachments(files, str(tmp_path))
    assert len(staged[0].read_paths) > 1
    assert all(p.endswith(".txt") for p in staged[0].read_paths)
    context = _document_context(files, staged)
    assert "可读分片" in context
    assert "先用 grep 定位" in context


def test_docx_attachment_text_extracted():
    from docx import Document

    doc = Document()
    doc.add_paragraph("合同金额：100 万")
    buf = io.BytesIO()
    doc.save(buf)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    text = extract_attachment_text("合同.docx", "", data)
    assert text is not None and "合同金额：100 万" in text


def test_long_attachment_is_truncated():
    files = [{"filename": "big.txt", "mime_type": "text/plain", "data": _b64("字" * 7000)}]
    context = _document_context(files)
    assert "[附件内容已截断]" in context
    assert len(context) < 7000


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
