"""单元测试：真实窗口绑定（Ollama 运行时窗口 → 预算/压缩/输出上限）。"""

from __future__ import annotations

from app.deps import initial_state
from models import catalog
from models.router import TierModel


def test_initial_state_binds_runtime_window(monkeypatch):
    monkeypatch.setattr(catalog, "effective_context_length", lambda mid, fallback=0: 16384)
    state = initial_state("hi", "u1", model="ollama_chat/deepseek-r1:7b")
    assert state["context_window"] == 16384
    # 阈值/保留量随真实窗口缩放（0.6 / 0.25）
    assert state["compress_threshold"] == int(16384 * 0.6)
    assert state["keep_recent_tokens"] == int(16384 * 0.25)


def test_initial_state_unknown_window_keeps_defaults(monkeypatch):
    monkeypatch.setattr(catalog, "effective_context_length", lambda mid, fallback=0: 0)
    state = initial_state("hi", "u1", model="ollama_chat/x")
    assert state["context_window"] == 0
    assert state["compress_threshold"] == 0
    assert state["keep_recent_tokens"] == 0


def test_ollama_max_tokens_shrinks_when_prompt_near_window(monkeypatch):
    monkeypatch.setattr(catalog, "effective_context_length", lambda mid, fallback=0: 4096)
    m = TierModel(max_tokens=8192)
    messages = [{"role": "user", "content": "x" * 20}]
    # 估算 prompt 很小，窗口 4096 → min(4096, 窗口-估算-256) 明显小于固定 4096
    got = m._max_tokens_for({"model": "ollama_chat/x"}, messages, None)
    assert got is not None
    assert 512 <= got < 4096


def test_cloud_model_max_tokens_unchanged(monkeypatch):
    m = TierModel(max_tokens=8192)
    messages = [{"role": "user", "content": "x" * 20}]
    assert m._max_tokens_for({"model": "deepseek/deepseek-v4-flash"}, messages, None) == 8192
