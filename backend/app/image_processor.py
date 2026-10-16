"""聊天图片附件回显：缩略图 + 描述（F8/F9 D2）。"""

from __future__ import annotations

import base64
import hashlib
import logging
import time as tmod
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})

DEFAULT_CAPTION_PROMPT = "简洁描述这张图片，突出可辨识的关键细节。"


def _pil_available() -> bool:
    try:
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def make_thumbnail(data_b64: str, px: int | None = None) -> str:
    """256px 缩略图 base64（回显用，避免传原图）。Pillow 缺失或失败时返回原图。"""
    if not _pil_available():
        return data_b64
    try:
        import io as _io

        from PIL import Image

        raw = base64.b64decode(data_b64 or "", validate=False)
        img = Image.open(_io.BytesIO(raw))
        box = max(1, int(px or settings.image_thumbnail_px or 256))
        img.thumbnail((box, box), Image.LANCZOS)
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return data_b64


def is_image_file(f: dict) -> bool:
    """按 mime / 扩展名判断是否为图片附件。"""
    from pathlib import Path

    if not isinstance(f, dict):
        return False
    mime = (f.get("mime_type") or "").lower()
    ext = Path(f.get("filename") or "").suffix.lower()
    return mime.startswith("image/") or ext in _IMAGE_EXTENSIONS


def ocr_image(data_b64: str, filename: str = "") -> str:
    """图片 OCR 提取文本（截图/文档图）。引擎缺失或未启用返回空串。"""
    if not settings.image_use_ocr:
        return ""
    try:
        import io as _io

        import pytesseract
        from PIL import Image

        raw = base64.b64decode(data_b64 or "", validate=False)
        img = Image.open(_io.BytesIO(raw))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        text = (text or "").strip()
        logger.info("image ocr ok (%s): %d chars", filename, len(text))
        return text
    except Exception as e:  # noqa: BLE001
        logger.warning("image ocr unavailable (%s): %s", filename, e)
        return ""


def _caption_kwargs() -> tuple[str, Optional[str], Optional[str], str, int]:
    model = settings.image_caption_model or ""
    api_base = settings.image_caption_api_base or None
    api_key = settings.image_caption_api_key or None
    prompt = settings.image_caption_prompt or DEFAULT_CAPTION_PROMPT
    timeout = max(5, int(settings.image_caption_timeout or 15))
    return model, api_base, api_key, prompt, timeout


_caption_cache: dict[str, str] = {}


def _img_fingerprint(data_b64: str) -> str:
    return hashlib.md5((data_b64 or "").encode("utf-8")).hexdigest()


async def caption_image(data_b64: str, mime_type: str = "", filename: str = "") -> str:
    """视觉 LLM 描述（IMAGE_CAPTION_MODEL），按输入指纹缓存，失败返回空串。"""
    import litellm

    model, api_base, api_key, prompt, timeout = _caption_kwargs()
    if not model or not data_b64:
        return ""
    fp = _img_fingerprint(data_b64)
    if fp in _caption_cache:
        return _caption_cache[fp]
    try:
        start = tmod.time()
        kwargs: dict = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type or 'image/jpeg'};base64,{data_b64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 200,
            "temperature": 0.2,
            "timeout": timeout,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        resp = await litellm.acompletion(**kwargs)
        content = (resp.choices[0].message.content or "").strip()
        if content:
            _caption_cache[fp] = content
        logger.info("image caption ok (%s, %.1fs): %s", filename, tmod.time() - start, content[:40])
        return content
    except Exception as e:  # noqa: BLE001
        logger.warning("image caption failed (%s): %s", filename, e)
        return ""


async def describe_image(data_b64: str, mime_type: str = "", filename: str = "") -> str:
    """caption（视觉 LLM）→ OCR → 空串，逐级降级。"""
    if settings.image_vlm_caption:
        cap = await caption_image(data_b64, mime_type, filename)
        if cap:
            return cap
    return ocr_image(data_b64, filename)


async def enrich_image_files(files: list | None) -> list:
    """图片附件附加回显字段：`_thumb`（缩略图 base64）与 `_caption`（描述）。

    非图片附件原样返回；缩略图/描述任一步失败都不阻断发送。
    """
    if not files:
        return files or []
    enriched: list = []
    for f in files:
        f = dict(f or {})
        if is_image_file(f):
            try:
                f["_thumb"] = make_thumbnail(f.get("data") or "")
            except Exception:  # noqa: BLE001
                pass
            try:
                cap = await describe_image(
                    f.get("data") or "", f.get("mime_type") or "", f.get("filename") or ""
                )
                if cap:
                    f["_caption"] = cap
            except Exception:  # noqa: BLE001
                pass
        enriched.append(f)
    return enriched
