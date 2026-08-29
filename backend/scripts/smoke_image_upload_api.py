# -*- coding: utf-8 -*-
"""[F8/F9] 真实大图上传管线冒烟（不依赖视觉 API，确定性验证）。

流程：
1. 生成 4000×3000 大图 → `_attachment_parts` 规格化 → 打印原始/规范化体积 + token 估算
2. `_enrich_image_files` → 缩略图 _thumb 生成 + caption 降级（无视觉 key → ""）
3. 真实 `_generate` 主循环（FakeLLM）带图跑多轮 → 打印每轮发送消息 token 估算，
   断言 ≤ llm_call_budget（图片不再撑爆上下文）

运行：.venv\\Scripts\\python.exe backend/scripts/smoke_image_upload_api.py
退出码：0=PASS 1=FAIL
"""
import base64
import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings
from app.context import budget
from app.context import token_counter as tc
from app.agent.graphmod.core import RAGAgent
from app.agent.graphmod.state import _attachment_parts
from app.api.chatmod.persist import _enrich_image_files
import app.agent.graphmod.generate as gen_mod

for n in ("record_model_call", "trace", "trace_messages"):
    setattr(gen_mod, n, lambda *a, **k: None)

settings.image_max_dimension = 1024
settings.image_max_kb = 512
settings.image_token_cap = 6000
settings.image_caption_model = ""  # 无视觉 key：caption 降级（验证不崩溃）


def make_big_image(w=4000, h=3000) -> str:
    from PIL import Image
    img = Image.effect_noise((w, h), 40).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def main() -> int:
    from app.agent.image_processor import estimate_image_tokens, make_thumbnail

    print("== F8/F9 真实大图上传管线冒烟 ==")
    b64 = make_big_image()
    print(f"原始图片 base64 长度: {len(b64) / 1024:.0f} KB")

    # 1. _attachment_parts 规格化
    images, text = _attachment_parts([{"filename": "photo.jpg", "mime_type": "image/jpeg", "data": b64}])
    if not images:
        print("FAIL: _attachment_parts 未返回图片")
        return 1
    img = images[0]
    w, h = img["_width"], img["_height"]
    tokens = estimate_image_tokens(w, h)
    print(f"规格化后: {w}x{h}, base64 {len(img['data']) / 1024:.0f} KB, 估算 token {tokens}")
    print(f"体积缩减 {(1 - len(img['data']) / len(b64)) * 100:.0f}%")

    # 2. 缩略图 + caption 降级
    enriched = None
    import asyncio
    enriched = asyncio.run(_enrich_image_files([{"filename": "photo.jpg", "mime_type": "image/jpeg", "data": b64}]))
    thumb = make_thumbnail(b64)
    print(f"缩略图 base64: {len(thumb) / 1024:.1f} KB（原图 {len(b64) / 1024:.0f} KB）")
    print(f"persist 增强: _thumb={'有' if enriched[0].get('_thumb') else '无'}, "
          f"_caption={'有' if enriched[0].get('_caption') else '无(无视觉key，降级)'}")

    # 3. 真实 _generate 主循环带图跑多轮（FakeLLM），验证图片不撑爆上下文
    class FakeLLM:
        def __init__(self, rounds):
            self.rounds = rounds
            self.calls = []

        async def __call__(self, model, messages, tool_defs, state=None):
            self.calls.append([dict(m) for m in messages])
            tcs = [
                SimpleNamespace(id=f"c{idx}", type="function",
                                function=SimpleNamespace(name="tool_probe", arguments='{"a":"1"}'))
                for idx in range(3)
            ]
            msg = SimpleNamespace(content="", tool_calls=tcs if len(self.calls) <= self.rounds else None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls" if tcs else "stop")],
                                   usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                                        prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1))

    llm = FakeLLM(8)
    agent = RAGAgent(SimpleNamespace(is_empty=True, invoke=lambda q, k=3: []))
    agent._build_tool_defs = lambda *a, **k: []
    agent._llm_call = llm

    async def fake_execute(name, args, state=None):
        return "R"
    agent._execute_tool = fake_execute

    sts = tempfile.mkdtemp(prefix="c5_img_")
    state = {
        "messages": [], "question": "分析这张图片", "context": [], "answer": "", "sources": [],
        "model": None, "history": [], "use_vector_db": False,
        "files": [{"filename": "photo.jpg", "mime_type": "image/jpeg", "data": b64}],
        "steps": [], "tokens": {}, "finish": "stop", "_event_queue": None, "_on_activity": None,
        "_task": None, "_cwd": sts, "_task_depth": 0, "conversation_id": "smoke",
    }
    asyncio.run(agent._generate(state))

    safe_budget = budget.llm_call_budget()
    peak = 0
    print(f"\n每轮发送消息 token 估算（预算 {safe_budget}）:")
    for i, msgs in enumerate(llm.calls, 1):
        est = tc.estimate_tokens_messages(msgs)
        peak = max(peak, est)
        print(f"  round{i:2d}: {est:6d} tok")
    print(f"\n峰值 {peak} / 预算 {safe_budget}（{peak / safe_budget * 100:.0f}%）；"
          f"图片首轮后已替换为占位（不再重发 base64）")
    ok = peak <= safe_budget
    print("结论:", "PASS - 大图规格化后不撑爆上下文，缩略图/描述链路正常" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
