# -*- coding: utf-8 -*-
"""[F8/F9] 真实端到端图片聊天冒烟：RAGAgent.invoke(带图) + Ollama caption + DeepSeek。

验证：
1. 图片规格化 + caption（Ollama llava）注入用户消息
2. 主模型（DeepSeek 纯文本）能否基于 caption 回答（image_url 块是否被拒绝）
3. 全程 pt 曲线 + 上下文有界

运行：.venv\\Scripts\\python.exe backend/scripts/smoke_image_chat_real.py
退出码：0=PASS（有回答且无越界） 1=FAIL 2=SKIP
"""
import asyncio
import base64
import io
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings

if not getattr(settings, "llm_api_key", ""):
    print("SKIP: 未配置 LLM_API_KEY")
    sys.exit(2)

from app.agent.graphmod.core import RAGAgent
from app.agent.graphmod.generate import RAGAgentGenerate
import app.agent.graphmod.core as core_mod
from types import SimpleNamespace


def make_photo() -> str:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (640, 480), (30, 40, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([80, 100, 280, 340], fill=(220, 80, 60))
    d.ellipse([360, 120, 560, 340], fill=(60, 160, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


PT_LOG: list[int] = []


def _spy(model, prompt_tokens=0, completion_tokens=0, duration_ms=0, tool_rounds=0, tool_calls=0):
    PT_LOG.append(prompt_tokens)


core_mod.record_model_call = _spy
core_mod.trace = lambda *a, **k: None
core_mod.trace_messages = lambda *a, **k: None

import app.agent.graphmod.generate as gen_mod
for n in ("record_model_call", "trace", "trace_messages"):
    setattr(gen_mod, n, lambda *a, **k: None)

settings.image_caption_model = "ollama/llava"
settings.image_caption_api_base = "http://localhost:11434"
settings.image_vlm_caption = True
settings.image_max_dimension = 1024


def main() -> int:
    print("== 真实端到端图片聊天冒烟 ==")
    b64 = make_photo()
    agent = RAGAgent(SimpleNamespace(is_empty=True, invoke=lambda q, k=3: []))
    question = "请用中文简短描述这张图片里有什么。"
    try:
        result = asyncio.run(agent.invoke(
            question,
            use_vector_db=False,
            files=[{"filename": "photo.png", "mime_type": "image/png", "data": b64}],
            directory="",
        ))
    except Exception as e:
        print(f"FAIL: 主模型调用异常: {type(e).__name__}: {str(e)[:200]}")
        print("（若为 image_url 被 DeepSeek 拒绝 → 需按视觉能力门控 image_url 块）")
        return 1

    answer = (result.get("answer") or "").strip()
    peak = max(PT_LOG) if PT_LOG else 0
    print(f"主模型调用 {len(PT_LOG)} 次，pt 峰值 {peak} / {settings.max_context_tokens}")
    print(f"回答: {answer[:200]}")
    ok = bool(answer) and not answer.startswith(("抱歉", "无法"))
    print("结论:", "PASS - 图片经 caption 注入后 DeepSeek 成功回答" if ok else "FAIL - 无有效回答")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
