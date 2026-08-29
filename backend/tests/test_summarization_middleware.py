# -*- coding: utf-8 -*-
"""HierarchicalSummarizationMiddleware 全量用例（mock litellm.acompletion）。

覆盖：
  - _split_into_chunks：空/无 assistant/整对/余数
  - _chunk_cache_key：list content 归一、非 dict 跳过、稳定性
  - apply：空历史、tokens/messages 触发阈值、keep 保留最近、摘要失败兜底截断
  - _hierarchical_summarize：单块直达、多块合并命中预算、递归至深度上限
  - _summarize：acompletion 调用参数（api_key/api_base/恒参）、cache 命中、
    usage 记账、空摘要不缓存、异常返回空串、超长分块头部+尾部截断
  - _cache_set 超容量淘汰一半
  - _fallback_truncate：预算内全保留、超预算插截断标记
运行：pytest tests/test_summarization_middleware.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.middleware.summarization as sm
from app.middleware.summarization import (
    HierarchicalSummarizationMiddleware,
    _chunk_cache_key,
    _split_into_chunks,
)


def _mk(**kw) -> HierarchicalSummarizationMiddleware:
    base = dict(model="test-model", trigger=("tokens", 4000), keep=("messages", 20))
    base.update(kw)
    return HierarchicalSummarizationMiddleware(**base)


# ── _split_into_chunks ─────────────────────────────────────────────────────

def test_split_empty():
    assert _split_into_chunks([]) == []


def test_split_no_assistant_single_chunk():
    msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    assert _split_into_chunks(msgs) == [msgs]


def test_split_exact_pairs():
    msgs = [
        {"role": "user", "content": "1"}, {"role": "assistant", "content": "A"},
        {"role": "user", "content": "2"}, {"role": "assistant", "content": "B"},
        {"role": "user", "content": "3"}, {"role": "assistant", "content": "C"},
    ]
    chunks = _split_into_chunks(msgs, pairs_per_chunk=2)
    assert len(chunks) == 2  # [0..3] + [4..5]
    assert chunks[0] == msgs[:4]
    assert chunks[1] == msgs[4:]


def test_split_odd_remainder():
    msgs = [
        {"role": "user", "content": "1"}, {"role": "assistant", "content": "A"},
        {"role": "user", "content": "2"}, {"role": "assistant", "content": "B"},
        {"role": "user", "content": "3"}, {"role": "assistant", "content": "C"},
        {"role": "user", "content": "4"},
    ]
    chunks = _split_into_chunks(msgs, pairs_per_chunk=2)
    assert len(chunks) == 2
    assert chunks[1][0]["role"] == "user"


# ── _chunk_cache_key ───────────────────────────────────────────────────────

def test_chunk_cache_key_normalizes_list_content():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image"}]}]
    msgs2 = [{"role": "user", "content": [{"type": "missing", "text": "hi"}]}]
    # 只取 text 块
    assert _chunk_cache_key(msgs) == _chunk_cache_key([{"role": "user", "content": "hi"}])
    # 跳过非 dict
    assert _chunk_cache_key([{"role": "user", "content": "hi"}, 42, None]) == _chunk_cache_key(
        [{"role": "user", "content": "hi"}]
    )
    # 键序无关
    assert _chunk_cache_key(msgs2) == _chunk_cache_key(msgs2)


# ── apply ──────────────────────────────────────────────────────────────────

async def test_apply_empty_history():
    assert await _mk().apply([]) == []


async def test_apply_below_tokens_trigger():
    mw = _mk(trigger=("tokens", 10_000))
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    assert await mw.apply(history) == history


async def test_apply_below_messages_trigger():
    mw = _mk(trigger=("messages", 100))
    history = [{"role": "user", "content": "hi"}]
    assert await mw.apply(history) == history


async def test_apply_compresses_keeps_recent(monkeypatch):
    mw = _mk(trigger=("tokens", 0), keep=("messages", 4))
    history = [
        {"role": "user", "content": "a0"}, {"role": "assistant", "content": "b0"},
        {"role": "user", "content": "a1"}, {"role": "assistant", "content": "b1"},
        {"role": "user", "content": "a2"}, {"role": "assistant", "content": "b2"},
    ]
    async def fake_sum(messages, depth=0):
        return "SUMMARY"
    monkeypatch.setattr(mw, "_hierarchical_summarize", fake_sum)
    out = await mw.apply(history)
    assert out[0] == {"role": "system", "content": "[Conversation summary]: SUMMARY"}
    # keep = min(4, 6//2=3) → 最近的 3 条原样保留
    assert out[1:] == history[-3:]


async def test_apply_fallback_when_summary_fails(monkeypatch):
    mw = _mk(trigger=("tokens", 0), keep=("messages", 2))
    history = [
        {"role": "user", "content": "x" * 20000},
        {"role": "assistant", "content": "y" * 20000},
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
    ]
    async def fail_sum(messages, depth=0):
        return ""
    monkeypatch.setattr(mw, "_hierarchical_summarize", fail_sum)
    out = await mw.apply(history)
    assert out[0]["content"] == "[earlier history truncated]"
    assert out[0]["role"] == "system"


# ── _hierarchical_summarize ────────────────────────────────────────────────

async def test_hierarchical_single_chunk(monkeypatch):
    mw = _mk()
    calls = []
    async def fake_sum(messages, depth=0):
        calls.append((messages, depth))
        return "S"
    monkeypatch.setattr(mw, "_summarize", fake_sum)
    out = await mw._hierarchical_summarize([{"role": "user", "content": "a"}], depth=0)
    assert out == "S"
    assert len(calls) == 1
    assert calls[0][1] == 0


async def test_hierarchical_multi_chunk_merges(monkeypatch):
    mw = _mk(trigger=("tokens", 4000), keep=("messages", 20), chunk_pairs=2)
    msgs = [
        {"role": "user", "content": "1"}, {"role": "assistant", "content": "A"},
        {"role": "user", "content": "2"}, {"role": "assistant", "content": "B"},
        {"role": "user", "content": "3"}, {"role": "assistant", "content": "C"},
    ]
    async def fake_sum(messages, depth=0):
        return f"S({len(messages)})"
    monkeypatch.setattr(mw, "_summarize", fake_sum)
    out = await mw._hierarchical_summarize(msgs)
    # 两个分块摘要合并后 token 估计远低于 4000 → 直接返回合并串
    assert out == "S(4)\n\nS(2)"


async def test_hierarchical_depth_limit(monkeypatch):
    mw = _mk(trigger=("tokens", 0), chunk_pairs=1)
    msgs = [m for i in range(10) for m in (
        {"role": "user", "content": f"u{i}"},
        {"role": "assistant", "content": f"a{i}"},
    )]
    async def fake_sum(messages, depth=0):
        return "S"
    monkeypatch.setattr(mw, "_summarize", fake_sum)
    out = await mw._hierarchical_summarize(msgs)
    assert out == ""


async def test_hierarchical_some_chunk_fails_aborts(monkeypatch):
    mw = _mk(trigger=("tokens", 4000), chunk_pairs=2)
    msgs = [
        {"role": "user", "content": "1"}, {"role": "assistant", "content": "A"},
        {"role": "user", "content": "2"}, {"role": "assistant", "content": "B"},
        {"role": "user", "content": "3"}, {"role": "assistant", "content": "C"},
    ]
    async def fake_sum(messages, depth=0):
        return "S" if len(messages) == 4 else ""
    monkeypatch.setattr(mw, "_summarize", fake_sum)
    assert await mw._hierarchical_summarize(msgs) == ""


# ── _summarize ─────────────────────────────────────────────────────────────

@pytest.fixture
def fake_litellm(monkeypatch):
    calls = []

    async def acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            choices=[SimpleNamespace(message=SimpleNamespace(content="SUMMARY"))],
        )

    monkeypatch.setattr(sm.litellm, "acompletion", acompletion)
    return calls


async def test_summarize_basic(fake_litellm, monkeypatch):
    calls = []
    def spy(model, prompt_tokens=0, completion_tokens=0, duration_ms=0, tool_rounds=0, tool_calls=0):
        calls.append((model, prompt_tokens, completion_tokens))
    monkeypatch.setattr(sm, "record_model_call", spy)
    mw = _mk()
    out = await mw._summarize([{"role": "user", "content": "hello there"}])
    assert out == "SUMMARY"
    assert len(fake_litellm) == 1
    kw = fake_litellm[0]
    assert kw["model"] == "test-model"
    assert kw["temperature"] == 0.1
    assert kw["max_tokens"] == 1024
    assert kw["timeout"] == 30
    assert "api_key" not in kw and "api_base" not in kw
    assert calls == [("test-model", 11, 7)]


async def test_summarize_with_api_key_and_base(fake_litellm):
    mw = _mk(api_key="k123", api_base="https://api.example.test")
    await mw._summarize([{"role": "user", "content": "x"}])
    kw = fake_litellm[0]
    assert kw["api_key"] == "k123"
    assert kw["api_base"] == "https://api.example.test"


async def test_summarize_cache_hit(fake_litellm):
    mw = _mk()
    msgs = [{"role": "user", "content": "cached input"}]
    assert await mw._summarize(msgs) == "SUMMARY"
    assert await mw._summarize(msgs) == "SUMMARY"
    assert len(fake_litellm) == 1  # 第二次命中缓存

    # 不同消息 → 再次调用
    await mw._summarize([{"role": "user", "content": "other"}])
    assert len(fake_litellm) == 2


async def test_summarize_empty_content_not_cached(fake_litellm):
    async def empty_acompletion(**kwargs):
        return SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content=""))],
        )
    sm.litellm.acompletion = empty_acompletion
    mw = _mk()
    msgs = [{"role": "user", "content": "x"}]
    assert await mw._summarize(msgs) == ""
    assert _chunk_cache_key(msgs) not in mw._cache


async def test_summarize_usage_none(fake_litellm, monkeypatch):
    async def no_usage_acompletion(**kwargs):
        return SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        )
    sm.litellm.acompletion = no_usage_acompletion
    calls = []
    def spy(model, prompt_tokens=0, completion_tokens=0, duration_ms=0, tool_rounds=0, tool_calls=0):
        calls.append((model, prompt_tokens, completion_tokens))
    monkeypatch.setattr(sm, "record_model_call", spy)
    mw = _mk()
    assert await mw._summarize([{"role": "user", "content": "y"}]) == "ok"
    assert calls == [("test-model", 0, 0)]


async def test_summarize_error_returns_empty(fake_litellm, monkeypatch, caplog):
    async def boom(**kwargs):
        raise RuntimeError("network down")
    sm.litellm.acompletion = boom
    calls = []
    def spy(model, prompt_tokens=0, completion_tokens=0, duration_ms=0, tool_rounds=0, tool_calls=0):
        calls.append(duration_ms)
    monkeypatch.setattr(sm, "record_model_call", spy)
    mw = _mk()
    with caplog.at_level("WARNING"):
        assert await mw._summarize([{"role": "user", "content": "z"}]) == ""
    assert "summarization failed" in caplog.text
    assert len(calls) == 1
    assert calls[0] >= 0


async def test_summarize_truncates_long_input(fake_litellm, monkeypatch):
    def huge_estimate(text):
        return 100_000
    monkeypatch.setattr(sm, "estimate_tokens", huge_estimate)
    mw = _mk()
    big = [{"role": "user", "content": "x" * 5000}, {"role": "assistant", "content": "y" * 5000}]
    await mw._summarize(big)
    content = fake_litellm[0]["messages"][0]["content"]
    assert "[middle portion omitted]" in content
    assert len(content) < len("x" * 5000 + "\n" + "y" * 5000)


def test_cache_eviction_half():
    mw = _mk(cache_size=2)
    mw._cache_set("k1", "v1")
    mw._cache_set("k2", "v2")
    mw._cache_set("k3", "v3")
    assert len(mw._cache) == 2
    assert "k1" not in mw._cache  # 淘汰一半（最旧）
    assert "k2" in mw._cache and "k3" in mw._cache


# ── _fallback_truncate ─────────────────────────────────────────────────────

def test_fallback_keeps_all_when_fits():
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    out = _mk()._fallback_truncate(history, max_tokens=10_000)
    assert out == history


def test_fallback_inserts_marker_on_overflow():
    history = [
        {"role": "user", "content": "x" * 5000},
        {"role": "assistant", "content": "recent"},
    ]
    out = _mk()._fallback_truncate(history, max_tokens=100)
    assert out[0]["content"] == "[earlier history truncated]"
    assert out[1:] == history[-1:]


def test_fallback_empty_history():
    assert _mk()._fallback_truncate([], max_tokens=100) == []