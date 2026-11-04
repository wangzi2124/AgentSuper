"""L1 单元测试：图片 OCR（tesseract 可配置路径 + 语言回退）。"""

import base64
import io
import shutil

import pytest

from app import image_processor as ip
from config import settings


def _text_png_b64(text: str, font_size: int = 40) -> str:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (760, 120), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in (r"C:\Windows\Fonts\arial.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(candidate, font_size)
            break
        except Exception:  # noqa: BLE001
            continue
    draw.text((20, 30), text, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _tesseract_available() -> bool:
    cmd = (settings.tesseract_cmd or "").strip()
    if cmd:
        import os

        return os.path.exists(cmd)
    return shutil.which("tesseract") is not None


def test_ocr_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "image_use_ocr", False)
    assert ip.ocr_image(_text_png_b64("Hello"), "a.png") == ""


def test_configure_tesseract_applies_settings(monkeypatch):
    monkeypatch.setattr(settings, "tesseract_cmd", r"C:\fake\tesseract.exe")
    monkeypatch.setattr(settings, "tessdata_dir", r"C:\fake\tessdata")
    monkeypatch.setattr(ip, "_tesseract_configured", False)

    class _Fake:
        class pytesseract:  # noqa: N801
            tesseract_cmd = "tesseract"

    import os

    ip._configure_tesseract(_Fake)
    try:
        assert _Fake.pytesseract.tesseract_cmd == r"C:\fake\tesseract.exe"
        assert os.environ["TESSDATA_PREFIX"] == r"C:\fake\tessdata"
    finally:
        os.environ.pop("TESSDATA_PREFIX", None)
        monkeypatch.setattr(ip, "_tesseract_configured", False)


def test_ocr_lang_attempts_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "ocr_languages", "chi_sim+eng")
    assert ip._ocr_lang_attempts() == ["chi_sim+eng", "eng"]
    monkeypatch.setattr(settings, "ocr_languages", "eng")
    assert ip._ocr_lang_attempts() == ["eng"]
    monkeypatch.setattr(settings, "ocr_languages", "")
    assert ip._ocr_lang_attempts() == ["eng"]


@pytest.mark.asyncio
async def test_describe_prefers_ocr_over_caption(monkeypatch):
    monkeypatch.setattr(ip, "ocr_image", lambda *a, **k: "OCR TEXT")
    called = {"caption": 0}

    async def _cap(*a, **k):
        called["caption"] += 1
        return "CAPTION TEXT"

    monkeypatch.setattr(ip, "caption_image", _cap)
    monkeypatch.setattr(settings, "image_vlm_caption", True)
    assert await ip.describe_image("QQ==") == "OCR TEXT"
    assert called["caption"] == 0


@pytest.mark.asyncio
async def test_describe_falls_back_to_caption_when_ocr_empty(monkeypatch):
    monkeypatch.setattr(ip, "ocr_image", lambda *a, **k: "")
    called = {"caption": 0}

    async def _cap(*a, **k):
        called["caption"] += 1
        return "CAPTION TEXT"

    monkeypatch.setattr(ip, "caption_image", _cap)
    monkeypatch.setattr(settings, "image_vlm_caption", True)
    assert await ip.describe_image("QQ==") == "CAPTION TEXT"
    assert called["caption"] == 1


@pytest.mark.asyncio
async def test_describe_skips_caption_when_disabled(monkeypatch):
    monkeypatch.setattr(ip, "ocr_image", lambda *a, **k: "")
    called = {"caption": 0}

    async def _cap(*a, **k):
        called["caption"] += 1
        return "CAPTION TEXT"

    monkeypatch.setattr(ip, "caption_image", _cap)
    monkeypatch.setattr(settings, "image_vlm_caption", False)
    assert await ip.describe_image("QQ==") == ""
    assert called["caption"] == 0


@pytest.mark.skipif(not _tesseract_available(), reason="tesseract binary not available")
def test_ocr_reads_text(monkeypatch):
    monkeypatch.setattr(settings, "image_use_ocr", True)
    monkeypatch.setattr(settings, "ocr_languages", "eng")
    monkeypatch.setattr(ip, "_tesseract_configured", False)
    text = ip.ocr_image(_text_png_b64("Hello OCR 2026"), "t.png")
    assert "Hello" in text.replace("\n", " ")

@pytest.mark.asyncio
async def test_caption_reuses_registered_provider_credentials(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(settings, "image_caption_model", "deepseek/deepseek-v4-flash")
    monkeypatch.setattr(settings, "image_caption_api_key", "")
    monkeypatch.setattr(settings, "image_caption_api_base", "")
    monkeypatch.setattr(
        "models.catalog.read_providers",
        lambda: {"deepseek": {"api_base": "https://api.deepseek.com", "api_key": "sk-test"}},
    )
    monkeypatch.setattr(
        "models.catalog.provider_api",
        lambda _mid: {
            "api_base": "https://api.deepseek.com",
            "api_key": "sk-test",
            "litellm_model": None,
            "options": {"thinking": {"type": "disabled"}},
        },
    )
    monkeypatch.setattr(
        "models.catalog.litellm_extra_kwargs",
        lambda creds: {"thinking": {"type": "disabled"}},
    )
    captured: dict = {}

    async def _fake(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="一张接口文档截图"))]
        )

    import litellm

    monkeypatch.setattr(litellm, "acompletion", _fake)
    ip._caption_cache.clear()
    out = await ip.caption_image("QUJD", "image/png")
    assert out == "一张接口文档截图"
    assert captured["api_key"] == "sk-test"
    assert captured["api_base"] == "https://api.deepseek.com"
    assert captured["thinking"] == {"type": "disabled"}
