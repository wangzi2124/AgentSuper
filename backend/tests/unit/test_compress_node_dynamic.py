"""压缩节点按调用时配置解析 keep_messages / model（支持 API 热更新）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from config import settings
from graph.nodes import compress as compress_mod


def test_keep_recent_reads_settings_at_call_time(monkeypatch):
    captured = {}

    async def fake_compress(messages, *, existing_summary, model, compress_count, keep_recent):
        captured.update(model=model, keep_recent=keep_recent)
        return SimpleNamespace(
            summary="s", compress_count=1, tail_start_id=None, recent=[]
        )

    monkeypatch.setattr(compress_mod, "compress_graph_messages", fake_compress)
    monkeypatch.setattr(settings, "summarization_keep_messages", 3)

    node = compress_mod.make_compress_node(None)
    asyncio.run(node({"messages": [object() for _ in range(10)]}))

    assert captured["keep_recent"] == 3


def test_model_factory_is_called_per_invocation(monkeypatch):
    captured = []
    sentinel = object()

    async def fake_compress(messages, *, existing_summary, model, compress_count, keep_recent):
        captured.append(model)
        return SimpleNamespace(
            summary="s", compress_count=1, tail_start_id=None, recent=[]
        )

    monkeypatch.setattr(compress_mod, "compress_graph_messages", fake_compress)
    monkeypatch.setattr(settings, "summarization_keep_messages", 1)

    node = compress_mod.make_compress_node(None, model_factory=lambda: sentinel)
    asyncio.run(node({"messages": [object() for _ in range(4)]}))

    assert captured == [sentinel]
