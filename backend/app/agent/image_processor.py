"""[F8/F9] 图片上传解析给模型 —— 规格化 / token 预算 / 描述桥（caption/OCR）。

三级管线 A 步：上传图片先规格化（等比缩放 + JPEG 压缩）再投递给模型，
避免大图 base64（4K 原图 ≈ 5–8MB 文本 ≈ 数十万 token）撑爆上下文。

- `normalize_image`：Pillow 缩放/压缩（无 Pillow 则原样返回 + 尺寸尽力上报，不阻断）
- `estimate_image_tokens`：按 OpenAI 视觉 token 面积公式估算（512×512 基准 85 + 每块 +170）
- `caption_image`（async）：图片描述桥——视觉 LLM 一次性生成 caption（litellm，
  独立 IMAGE_CAPTION_MODEL/API_BASE/KEY，与主对话模型隔离；兼容 OpenAI 兼容接口）
- `ocr_image`：OCR 钩子（`IMAGE_USE_OCR`，需 OCR 库；未启用/缺失返回 ""）
- `describe_image`（async）：降级链 caption → OCR → ""（主 Agent/子 Agent 通用）
"""

import base64
import logging
import time as tmod
from typing import Optional

logger = logging.getLogger(__name__)

# OpenAI 视觉 token 公式：512×512 基准 85 token，每额外 512 块 +170
_IMG_BASE_TOKENS = 85
_IMG_BLOCK_TOKENS = 170
_IMG_BLOCK_PX = 512

DEFAULT_CAPTION_PROMPT = "用中文简洁描述这张图片的内容和关键细节，不超过 60 字。"


def estimate_image_tokens(width: int, height: int) -> int:
    """按 OpenAI 视觉 token 面积公式估算单张图片的 token 数。"""
    if width <= 0 or height <= 0:
        return _IMG_BASE_TOKENS
    blocks_w = (width + _IMG_BLOCK_PX - 1) // _IMG_BLOCK_PX
    blocks_h = (height + _IMG_BLOCK_PX - 1) // _IMG_BLOCK_PX
    return _IMG_BASE_TOKENS + _IMG_BLOCK_TOKENS * blocks_w * blocks_h


def _pil_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def normalize_image(
    data_b64: str,
    mime_type: str = "",
    filename: str = "",
    max_dimension: Optional[int] = None,
    max_kb: Optional[int] = None,
) -> dict:
    """把 base64 图片规格化：等比缩放到 max_dimension + JPEG 压到 max_kb。

    返回 {"data": 新 base64, "mime_type", "filename", "width", "height",
          "original_size", "resized"}。Pillow 缺失时原样返回（width/height 尽力读）。
    max_dimension/max_kb 覆盖 settings（token 超限降采样时传更小值）。
    """
    from app.config import settings

    try:
        raw = base64.b64decode(data_b64 or "", validate=False)
    except Exception:  # noqa: BLE001 —— 坏 base64（非 4 倍数等）不阻断，原样返回
        return {
            "data": data_b64,
            "mime_type": mime_type or "image/jpeg",
            "filename": filename,
            "width": 0,
            "height": 0,
            "original_size": 0,
            "resized": False,
        }
    original_size = len(raw)
    width = height = 0
    result_data = data_b64
    result_mime = mime_type or "image/jpeg"
    resized = False

    if _pil_available():
        try:
            import io as _io
            from PIL import Image
            img = Image.open(_io.BytesIO(raw))
            width, height = img.size
            max_dim = max(1, int(max_dimension or settings.image_max_dimension))
            if max(width, height) > max_dim:
                ratio = max_dim / max(width, height)
                img = img.resize((max(1, int(width * ratio)), max(1, int(height * ratio))), Image.LANCZOS)
                width, height = img.size
                resized = True
            buf = _io.BytesIO()
            img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=85)
            out = buf.getvalue()
            max_kb_val = max(1, int(max_kb or settings.image_max_kb))
            # 逐级降质直到 ≤ max_kb（最多 3 次）
            for q in (75, 55):
                if len(out) <= max_kb_val * 1024:
                    break
                buf = _io.BytesIO()
                img.save(buf, format="JPEG", quality=q)
                out = buf.getvalue()
            if len(out) < original_size:
                result_data = base64.b64encode(out).decode("ascii")
                result_mime = "image/jpeg"
                resized = True
        except Exception as e:  # noqa: BLE001 —— 解析失败不阻断（原样返回）
            logger.warning("image normalize failed (%s): %s", filename, e)
    else:
        # 无 Pillow：尽力从 base64 头读尺寸（PNG/JPEG/GIF/WebP）
        width, height = _peek_dimensions(raw)

    return {
        "data": result_data,
        "mime_type": result_mime,
        "filename": filename,
        "width": width,
        "height": height,
        "original_size": original_size,
        "resized": resized,
    }


def _peek_dimensions(raw: bytes) -> tuple[int, int]:
    """无 Pillow 时从图片头解析宽高（PNG/JPEG/GIF/WebP）；失败返回 (0,0)。"""
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
            return w, h
        if raw[:2] == b"\xff\xd8" and raw[6:8] == b"JFIF":
            i = 2
            while i < len(raw) - 9:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xC0, 0xC2):
                    h = int.from_bytes(raw[i + 5:i + 7], "big")
                    w = int.from_bytes(raw[i + 7:i + 9], "big")
                    return w, h
                size = int.from_bytes(raw[i + 2:i + 4], "big")
                i += 2 + size
        if raw[:6] in (b"GIF87a", b"GIF89a") and len(raw) >= 10:
            return int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" and len(raw) >= 30:
            return int.from_bytes(raw[26:28], "little"), int.from_bytes(raw[28:30], "little")
    except Exception:  # noqa: BLE001
        pass
    return 0, 0


def ocr_image(data_b64: str, filename: str = "") -> str:
    """图片 OCR 提取文本（截图/文档图，信息保真优先）。

    IMAGE_USE_OCR=false 或 OCR 库缺失时返回 ""（不启用）。接入示例：
    PaddleOCR / pytesseract；此处为 hook，落地具体引擎时在分支内实现。
    """
    from app.config import settings
    if not settings.image_use_ocr:
        return ""
    return ""  # TODO: 接入 OCR 引擎后实现


def _caption_kwargs() -> tuple[str, Optional[str], Optional[str], str, int]:
    from app.config import settings
    model = settings.image_caption_model or ""
    api_base = settings.image_caption_api_base or None
    api_key = settings.image_caption_api_key or None
    prompt = settings.image_caption_prompt or DEFAULT_CAPTION_PROMPT
    timeout = max(5, int(settings.image_caption_timeout or 15))
    return model, api_base, api_key, prompt, timeout


async def caption_image(data_b64: str, mime_type: str = "", filename: str = "") -> str:
    """图片描述桥：用视觉 LLM（IMAGE_CAPTION_MODEL）生成图片描述。

    兼容现有系统：走 litellm.acompletion，OpenAI 兼容接口（接受 image_url
    多模态消息）。失败（模型不支持/无 key/超时）返回 ""，由降级链接 OCR/占位。
    """
    import litellm
    model, api_base, api_key, prompt, timeout = _caption_kwargs()
    if not model or not data_b64:
        return ""
    try:
        start = tmod.time()
        kwargs: dict = {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{mime_type or 'image/jpeg'};base64,{data_b64}",
                    }},
                ],
            }],
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
        logger.info("image caption ok (%s, %.1fs): %s", filename, tmod.time() - start, content[:40])
        return content
    except Exception as e:  # noqa: BLE001
        logger.warning("image caption failed (%s): %s", filename, e)
        return ""


async def describe_image(data_b64: str, mime_type: str = "", filename: str = "") -> str:
    """降级链：caption（视觉 LLM）→ OCR → ""（占位由调用方兜底）。

    IMAGE_VLM_CAPTION=false 时跳过 caption 直接 OCR/占位。
    """
    from app.config import settings
    if settings.image_vlm_caption:
        cap = await caption_image(data_b64, mime_type, filename)
        if cap:
            return cap
    return ocr_image(data_b64, filename)


def is_image_file(f: dict) -> bool:
    """按 mime/扩展名判断是否为图片附件。"""
    from pathlib import Path
    from app.agent.attachment_loader import _IMAGE_EXTENSIONS
    mime = (f.get("mime_type") or "").lower()
    ext = Path(f.get("filename") or "").suffix.lower()
    return mime.startswith("image/") or ext in _IMAGE_EXTENSIONS


async def attachment_context_with_images(files: list[dict], budget: int = 3000) -> str:
    """[F8 · C 步] 子 Agent 附件上下文：文档正文 + 图片 caption/OCR 文本。

    让 web_search/code 等非视觉子 Agent 也能「看到」上传图片的内容
    （此前只给 `[图片附件]` 占位）。
    """
    from app.agent.attachment_loader import attachment_context_text

    text_files = [f for f in files if not is_image_file(f)]
    parts: list[str] = []
    if text_files:
        t = attachment_context_text(text_files, budget=budget)
        if t.strip():
            parts.append(t)
    caps: list[str] = []
    for f in files:
        if not is_image_file(f):
            continue
        cap = await describe_image(
            f.get("data") or "", f.get("mime_type") or "", f.get("filename") or "",
        )
        if cap:
            caps.append(f"[图片 {f.get('filename', '')}]: {cap}")
    if caps:
        parts.append("[图片描述]\n" + "\n".join(caps))
    return "\n\n".join(parts)


def make_thumbnail(data_b64: str, px: int = 256) -> str:
    """[F8 · D 步] 生成缩略图 base64（回显/缓存用，避免传原图）。Pillow 缺失返回原图。"""
    import base64 as _b64
    if not _pil_available():
        return data_b64
    try:
        import io as _io
        from PIL import Image
        raw = _b64.b64decode(data_b64 or "", validate=False)
        img = Image.open(_io.BytesIO(raw))
        img.thumbnail((px, px), Image.LANCZOS)
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        return _b64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001
        return data_b64
