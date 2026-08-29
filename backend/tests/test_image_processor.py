# -*- coding: utf-8 -*-
"""[F8/F9] image_processor 用例：规格化（缩放/压缩）、图片 token 估算、
caption 描述桥（mock litellm）、降级链、_peek_dimensions 头部解析。

运行：pytest tests/test_image_processor.py
"""
import base64
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import litellm

import pytest

from app.agent import image_processor as ip
from app.config import settings


def _b64bytes(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# ── token 估算 ─────────────────────────────────────────────────────────────

def test_estimate_image_tokens():
    assert ip.estimate_image_tokens(0, 0) == 85
    assert ip.estimate_image_tokens(512, 512) == 85 + 170
    assert ip.estimate_image_tokens(1024, 1024) == 85 + 170 * 4
    assert ip.estimate_image_tokens(768, 768) == 85 + 170 * 4  # 2×2 块
    assert ip.estimate_image_tokens(200, 200) == 85 + 170  # 1×1 块


# ── 规格化（Pillow 真实生成）───────────────────────────────────────────────

def _mk_image(w=2000, h=1200, color=(100, 150, 200)) -> bytes:
    """噪声图（模拟真实照片）：PNG 体积大，验证 JPEG 压缩确实减小体积。"""
    from PIL import Image
    import io as _io
    img = Image.effect_noise((w, h), 40).convert("RGB")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_normalize_image_downscales(monkeypatch):
    monkeypatch.setattr(settings, "image_max_dimension", 1024)
    monkeypatch.setattr(settings, "image_max_kb", 512)
    raw = _mk_image(2000, 1200)
    out = ip.normalize_image(_b64bytes(raw), "image/png", "big.png")
    assert out["resized"] is True
    assert max(out["width"], out["height"]) <= 1024
    # JPEG 输出
    assert out["mime_type"] == "image/jpeg"
    assert len(out["data"]) < len(_b64bytes(raw))


def test_normalize_image_small_unchanged_size_but_jpeg(monkeypatch):
    monkeypatch.setattr(settings, "image_max_dimension", 1024)
    raw = _mk_image(300, 200)
    out = ip.normalize_image(_b64bytes(raw), "image/png", "s.png")
    assert out["width"] == 300 and out["height"] == 200
    # 尺寸未变，但 JPEG 压缩后体积下降 → resized 置 True（体积优化也算）
    assert len(out["data"]) <= len(_b64bytes(raw))


def test_normalize_image_invalid_bytes_passthrough(monkeypatch):
    monkeypatch.setattr(settings, "image_max_dimension", 1024)
    data = "not-an-image"
    out = ip.normalize_image(data, "image/png", "bad.png")
    assert out["data"] == data  # 解析失败原样返回，不阻断


def test_normalize_image_with_max_dimension_override(monkeypatch):
    monkeypatch.setattr(settings, "image_max_dimension", 1024)
    raw = _mk_image(2000, 1200)
    out = ip.normalize_image(_b64bytes(raw), "image/png", "x.png", max_dimension=512)
    assert max(out["width"], out["height"]) <= 512


# ── _peek_dimensions 头部解析（无 Pillow 路径）─────────────────────────────

def test_peek_dimensions_png_jpeg_gif_webp():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (100).to_bytes(4, "big") + (80).to_bytes(4, "big")
    assert ip._peek_dimensions(png) == (100, 80)
    gif = b"GIF89a" + (12).to_bytes(2, "little") + (34).to_bytes(2, "little")
    assert ip._peek_dimensions(gif) == (12, 34)
    assert ip._peek_dimensions(b"garbage") == (0, 0)


# ── caption 描述桥 ─────────────────────────────────────────────────────────

def _b64_1x1() -> str:
    return _b64bytes(_mk_image(1, 1))


@pytest.fixture
def caption_on(monkeypatch):
    monkeypatch.setattr(settings, "image_vlm_caption", True)
    monkeypatch.setattr(settings, "image_caption_model", "openai/gpt-4o-mini")
    monkeypatch.setattr(settings, "image_caption_api_base", None)
    monkeypatch.setattr(settings, "image_caption_api_key", None)
    monkeypatch.setattr(settings, "image_caption_timeout", 5)


@pytest.mark.asyncio
async def test_caption_image_success(caption_on, monkeypatch):
    calls = {}

    async def fake_acompletion(**kw):
        calls.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="  一只猫在窗台上  "))])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    cap = await ip.caption_image(_b64_1x1(), "image/png", "cat.png")
    assert cap == "一只猫在窗台上"
    assert calls["model"] == "openai/gpt-4o-mini"
    assert calls["messages"][0]["content"][1]["type"] == "image_url"
    assert "data:image/png;base64," in calls["messages"][0]["content"][1]["image_url"]["url"]


@pytest.mark.asyncio
async def test_caption_image_disabled_or_fail(caption_on, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("no key")
    monkeypatch.setattr(litellm, "acompletion", boom)
    assert await ip.caption_image(_b64_1x1(), "image/png", "x.png") == ""

    monkeypatch.setattr(settings, "image_caption_model", "")
    assert await ip.caption_image("", "image/png", "x.png") == ""


@pytest.mark.asyncio
async def test_describe_image_chain(caption_on, monkeypatch):
    # caption 失败 → 回退 OCR（默认关）→ ""
    async def boom(**kw):
        raise RuntimeError("down")
    monkeypatch.setattr(litellm, "acompletion", boom)
    monkeypatch.setattr(settings, "image_use_ocr", False)
    assert await ip.describe_image(_b64_1x1(), "image/png", "x.png") == ""

    # image_vlm_caption=false → 跳过 caption
    async def cap_ok(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="CAP"))])
    monkeypatch.setattr(litellm, "acompletion", cap_ok)
    monkeypatch.setattr(settings, "image_vlm_caption", True)
    assert await ip.describe_image(_b64_1x1(), "image/png", "x.png") == "CAP"


def test_ocr_hook_disabled_by_default():
    assert ip.ocr_image(_b64_1x1(), "x.png") == ""


# ── C 步：子 Agent 附件上下文（文档 + 图片 caption）────────────────────────

def test_is_image_file():
    assert ip.is_image_file({"filename": "a.png", "mime_type": "image/png"}) is True
    assert ip.is_image_file({"filename": "b.JPG", "mime_type": "application/octet-stream"}) is True
    assert ip.is_image_file({"filename": "c.txt", "mime_type": "text/plain"}) is False


@pytest.mark.asyncio
async def test_attachment_context_with_images(caption_on, monkeypatch):
    async def fake_describe(data_b64="", mime_type="", filename=""):
        return "CAT" if "img" in filename else ""
    monkeypatch.setattr(ip, "describe_image", fake_describe)
    import app.agent.attachment_loader as al
    orig = al.attachment_context_text
    al.attachment_context_text = lambda files, budget=3000: "文档正文" if files else ""
    try:
        out = await ip.attachment_context_with_images([
            {"filename": "doc.txt", "mime_type": "text/plain", "data": "x"},
            {"filename": "img1.png", "mime_type": "image/png", "data": "y"},
        ])
    finally:
        al.attachment_context_text = orig
    assert "文档正文" in out
    assert "[图片 img1.png]: CAT" in out


# ── D 步：缩略图 ───────────────────────────────────────────────────────────

def test_make_thumbnail():
    raw = _mk_image(2000, 1200)
    thumb = ip.make_thumbnail(_b64bytes(raw), px=256)
    assert len(thumb) < len(_b64bytes(raw))
    from PIL import Image
    import io as _io
    img = Image.open(_io.BytesIO(base64.b64decode(thumb)))
    assert max(img.size) <= 256