# -*- coding: utf-8 -*-
"""chatmod/helpers.py 全量用例：用户身份、标题生成、历史截断/清洗、消息校验、
摘要中间件单例。

运行：pytest tests/test_chatmod_helpers.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi import HTTPException

import app.api.chatmod.helpers as h
from app.config import settings
from app.models.schemas import ChatRequest


# ── _get_user_id ───────────────────────────────────────────────────────────

def _req(headers=None, client=None, path="/api/chat/multi-agent"):
    return SimpleNamespace(
        headers=headers or {},
        client=client,
        url=SimpleNamespace(path=path),
    )


def test_get_user_id_header():
    assert h._get_user_id(_req(headers={"X-User-Id": "u1"})) == "u1"


def test_get_user_id_blank_defaults():
    assert h._get_user_id(_req(headers={"X-User-Id": "  "})) == "anonymous"
    assert h._get_user_id(_req(headers={})) == "anonymous"


def test_get_user_id_forwarded_for():
    req = _req(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}, client=SimpleNamespace(host="9.9.9.9"))
    assert h._get_user_id(req) == "anonymous"  # 只取 user_id，IP 仅日志


# ── _generate_title ────────────────────────────────────────────────────────

def test_generate_title_user_message():
    assert h._generate_title([{"role": "user", "content": "你好世界"}]) == "你好世界"


def test_generate_title_cleans_and_escapes():
    title = h._generate_title([{"role": "user", "content": "a\x00b\x07c\n换行<script>"}])
    assert "\x00" not in title and "\x07" not in title
    assert "换行" in title
    assert "&lt;script&gt;" in title  # html 转义


def test_generate_title_truncates():
    title = h._generate_title([{"role": "user", "content": "x" * 50}])
    assert title.endswith("...")


def test_generate_title_no_user():
    assert h._generate_title([{"role": "assistant", "content": "x"}]) == "新对话"


# ── _truncate_history ──────────────────────────────────────────────────────

def test_truncate_history_empty():
    assert h._truncate_history([]) == []


def test_truncate_history_fits_all():
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert h._truncate_history(history, max_tokens=10000) == history


def test_truncate_history_over_budget_keeps_last():
    history = [{"role": "user", "content": "x" * 3000} for _ in range(10)]
    out = h._truncate_history(history, max_tokens=3000)
    assert out[0]["role"] == "system"
    assert "[earlier history truncated]" in out[0]["content"]
    assert out[-1] == history[-1]


# ── _sanitize_history ──────────────────────────────────────────────────────

def test_sanitize_history_filters():
    history = [
        {"role": "user", "content": "hi", "id": "x1", "steps": [1], "sources": []},
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
        "not-a-dict",
        {"role": "bogus", "content": "drop"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c2"}]},
        {"role": "user", "content": None},
    ]
    out = h._sanitize_history(history)
    assert out[0] == {"role": "user", "content": "hi"}  # id/steps/sources 剥离
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "c1"
    assert any(m.get("tool_calls") for m in out)  # 仅 tool_calls 的消息保留
    assert len(out) == 3


# ── _msg_type_to_role ──────────────────────────────────────────────────────

def test_msg_type_to_role():
    assert h._msg_type_to_role("user") == "user"
    assert h._msg_type_to_role("assistant") == "assistant"
    assert h._msg_type_to_role("tool") == "tool"
    assert h._msg_type_to_role("compaction") == "system"
    assert h._msg_type_to_role("epoch") == "system"
    assert h._msg_type_to_role("system") == "system"
    assert h._msg_type_to_role("unknown") == "system"


# ── _validate_chat_message ─────────────────────────────────────────────────

def test_validate_chat_message_ok():
    h._validate_chat_message(ChatRequest(message="hello"))


def test_validate_chat_message_blank():
    # schema strip_whitespace 已拦截空白 → 用 model_construct 绕过，直测 handler 分支
    body = ChatRequest.model_construct(message="   ")
    with pytest.raises(HTTPException) as e:
        h._validate_chat_message(body)
    assert e.value.status_code == 422


def test_validate_chat_message_too_long(monkeypatch):
    monkeypatch.setattr(h, "MAX_MESSAGE_LENGTH", 10)
    with pytest.raises(HTTPException) as e:
        h._validate_chat_message(ChatRequest(message="x" * 11))
    assert e.value.status_code == 422


# ── _get_summarizer / reset ────────────────────────────────────────────────

def test_summarizer_none_when_no_model(monkeypatch):
    h.reset_summarizer()
    monkeypatch.setattr(settings, "summarization_model", "")
    assert h._get_summarizer() is None


def test_summarizer_creates_and_caches(monkeypatch):
    h.reset_summarizer()
    monkeypatch.setattr(settings, "summarization_model", "test-model")
    s1 = h._get_summarizer()
    assert s1 is not None and s1.model == "test-model"
    assert h._get_summarizer() is s1  # 缓存命中


def test_summarizer_invalidates_on_model_change(monkeypatch):
    h.reset_summarizer()
    monkeypatch.setattr(settings, "summarization_model", "model-a")
    s1 = h._get_summarizer()
    monkeypatch.setattr(settings, "summarization_model", "model-b")
    s2 = h._get_summarizer()
    assert s2 is not s1
    assert s2.model == "model-b"
    h.reset_summarizer()
    assert h._summarizer is None and h._summarizer_model is None


def test_get_session_service():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_service="SVC")))
    assert h._get_session_service(request) == "SVC"


def test_constants():
    assert h.MAX_MESSAGE_LENGTH == 50_000
    assert h.MAX_HISTORY_TOKENS >= 1