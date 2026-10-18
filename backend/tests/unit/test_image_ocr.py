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


@pytest.mark.skipif(not _tesseract_available(), reason="tesseract binary not available")
def test_ocr_reads_text(monkeypatch):
    monkeypatch.setattr(settings, "image_use_ocr", True)
    monkeypatch.setattr(settings, "ocr_languages", "eng")
    monkeypatch.setattr(ip, "_tesseract_configured", False)
    text = ip.ocr_image(_text_png_b64("Hello OCR 2026"), "t.png")
    assert "Hello" in text.replace("\n", " ")
