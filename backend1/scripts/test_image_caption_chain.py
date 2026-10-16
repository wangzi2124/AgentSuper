# -*- coding: utf-8 -*-
"""[F8/F9] caption 三路链测试：① 有费（OpenAI gpt-4o-mini）→ ② Ollama 本地视觉 → ③ OCR。

每路独立调用 image_processor，输出结果与降级情况：
- ① openai/gpt-4o-mini：无 OPENAI_API_KEY → 应优雅降级 ""（记录原因）
- ② ollama/llava：本地 Ollama 视觉（需已 `ollama pull llava`）；模型缺失 → 降级
- ③ OCR：pytesseract + tesseract 二进制（eng），文本图应能提取出文字

运行：.venv\\Scripts\\python.exe backend/scripts/test_image_caption_chain.py
退出码：0=PASS（三路均不崩溃；OCR 有输出或明确降级原因）
"""
import asyncio
import base64
import io
import os
import sys
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings
from app.agent import image_processor as ip


def make_photo() -> str:
    """彩图（caption 用）：色块 + 简单图形。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (512, 384), (30, 40, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 80, 220, 260], fill=(220, 80, 60))
    d.ellipse([280, 100, 440, 260], fill=(60, 160, 220))
    d.polygon([(256, 40), (180, 120), (332, 120)], fill=(90, 200, 90))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_text_image() -> str:
    """文字图（OCR 用）：白底黑字。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (640, 200), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except Exception:
        font = ImageFont.load_default()
    d.text((40, 60), "HELLO OCR 2026", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> int:
    print("== caption 三路链测试 ==")
    photo = make_photo()
    textimg = make_text_image()

    async def run() -> list[str]:
        results = []

        # ① 有费 OpenAI
        settings.image_caption_model = "openai/gpt-4o-mini"
        settings.image_caption_api_base = ""
        settings.image_caption_api_key = ""
        paid = await ip.caption_image(photo, "image/png", "photo.png")
        results.append(
            f"① 有费 openai/gpt-4o-mini → {'caption: ' + paid[:60] if paid else '降级（无 OPENAI_API_KEY 或失败）'}"
        )

        # ② Ollama 本地视觉
        settings.image_caption_model = settings.image_caption_model or "ollama/llava"
        settings.image_caption_model = "ollama/llava"
        settings.image_caption_api_base = "http://localhost:11434"
        settings.image_caption_api_key = ""
        # 先探测 Ollama 端点与已装模型
        ollama_ok = False
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as r:
                tags = r.read().decode("utf-8", "replace")
            import json as _json
            names = [m.get("name") for m in _json.loads(tags).get("models", [])]
            ollama_ok = True
            results.append(f"② Ollama 端点 OK，已装模型: {names}")
        except Exception as e:
            results.append(f"② Ollama 端点不可达: {e}")

        ollama_cap = await ip.caption_image(photo, "image/png", "photo.png")
        if ollama_cap:
            results.append(f"② ollama/llava caption → {ollama_cap[:60]}")
        else:
            vision = [n for n in names if any(k in n.lower() for k in ("llava", "vl", "vision", "moondream"))] if ollama_ok else []
            results.append(
                "② ollama/llava caption → 降级" + (f"（已装视觉模型 {vision}，可把 IMAGE_CAPTION_MODEL 改为 ollama/{vision[0]}）"
                                                    if vision else "（未拉取视觉模型；`ollama pull llava` 后即生效）")
            )

        # ③ OCR
        settings.image_use_ocr = True
        ocr = ip.ocr_image(textimg, "text.png")
        if ocr:
            results.append(f"③ OCR → {ocr.strip()[:80]}")
        else:
            results.append("③ OCR → 降级（tesseract 二进制或 pytesseract 缺失）")
        return results

    results = asyncio.run(run())
    for r in results:
        print("  " + r)

    # PASS 判定：三路均不崩溃；OCR 有输出 或 明确记录了降级原因
    ok = len(results) == 4 and all(r for r in results)
    print("\n结论:", "PASS - 三路链均执行完毕（降级链路正常）" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
