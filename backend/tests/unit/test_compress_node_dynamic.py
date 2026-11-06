"""压缩节点按调用时配置解析 keep_messages / model（支持 API 热更新）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from config import settings
from graph.nodes import compress as compress_mod


class _FakeCompactor:
    captured: dict = {}

    async def compress(self, messages, *, existing_summary="", model=None, policy=None, plan=None):
        type(self).captured.update(
            model=model,
            keep_recent=policy.keep_recent,
            compress_count=policy.compress_count,
            keep_tokens=policy.keep_tokens,
        )
        return SimpleNamespace(
            summary="s",
            compress_count=policy.compress_count,
            tail_start_id=None,
            recent=[],
            triggered=True,
        )


def test_keep_recent_reads_settings_at_call_time(monkeypatch):
    _FakeCompactor.captured = {}
    monkeypatch.setattr(compress_mod, "ContextCompactor", _FakeCompactor)
    monkeypatch.setattr(settings, "compaction_keep_tokens", 3)

    node = compress_mod.make_compress_node(None)
    asyncio.run(node({"messages": [object() for _ in range(10)]}))

    assert _FakeCompactor.captured["keep_recent"] == 3


def test_model_factory_is_called_per_invocation(monkeypatch):
    captured = []
    sentinel = object()

    class _CapturingCompactor(_FakeCompactor):
        async def compress(self, messages, *, existing_summary="", model=None, policy=None, plan=None):
            captured.append(model)
            return await super().compress(
                messages,
                existing_summary=existing_summary,
                model=model,
                policy=policy,
                plan=plan,
            )

    monkeypatch.setattr(compress_mod, "ContextCompactor", _CapturingCompactor)
    monkeypatch.setattr(settings, "compaction_keep_tokens", 1)

    node = compress_mod.make_compress_node(None, model_factory=lambda: sentinel)
    asyncio.run(node({"messages": [object() for _ in range(4)]}))

    assert captured == [sentinel]
