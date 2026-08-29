# -*- coding: utf-8 -*-
"""graphmod state/constants/base 纯逻辑用例（无 LLM 调用）。

覆盖：
  - state.py：_extract_cache_usage（litellm/DeepSeek 双字段 + miss 兜底）、
    _find_attachment、_attachment_parts（图片/文本分流）、_ZERO_USAGE
  - constants.py：_normalize_finish_reason 六值归一、_nearest_workspace_hint、
    _permission_denied_msg、_is_multi_agent_queue、常量集合
  - base.py：_activity_text、_push_event、_retrieve、_rerank、
    _system_prompt_with_kb、_tool_matches_intent、_build_tool_defs（pinned/used/核心/
    意图）、_pinned_tool_names、_bound_plugin_result

已知产品缺陷（已按现状锁定，待修）：`state._attachment_parts` 的 `from . import
attachment_loader` 在 graphmod 子包下不存在 → 含非图片附件的请求会抛 ImportError。
运行：pytest tests/test_graphmod_state_constants_base.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from _graphmod_support import build_agent, make_state
from app.agent.graphmod.constants import (
    DOOM_LOOP_PROMPT,
    MAX_STEPS_PROMPT,
    _DEDUP_READONLY_TOOLS,
    _FINISH_REASON_MAP,
    _TASK_TOOL_SCHEMA,
    _TASK_TOOL_SUBAGENTS,
    _is_multi_agent_queue,
    _nearest_workspace_hint,
    _normalize_finish_reason,
    _permission_denied_msg,
)
from app.agent.graphmod.state import (
    _ZERO_USAGE,
    _attachment_parts,
    _extract_cache_usage,
    _find_attachment,
)


# ── state.py ───────────────────────────────────────────────────────────────

def test_extract_cache_usage_none():
    assert _extract_cache_usage(None) == (0, 0)
    assert _extract_cache_usage(None, pt=10) == (0, 0)


def test_extract_cache_usage_litellm_fields():
    u = SimpleNamespace(prompt_cache_hit_tokens=7, prompt_cache_miss_tokens=3, prompt_tokens=10)
    assert _extract_cache_usage(u, pt=10) == (7, 3)


def test_extract_cache_usage_deepseek_details():
    det = SimpleNamespace(cached_tokens=6)
    u = SimpleNamespace(prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0, prompt_tokens_details=det)
    assert _extract_cache_usage(u, pt=10) == (6, 4)  # miss 兜底 pt-hit


def test_extract_cache_usage_miss_fallback_only_when_pt_gt_hit():
    u = SimpleNamespace(prompt_cache_hit_tokens=4, prompt_cache_miss_tokens=0, prompt_tokens=10)
    assert _extract_cache_usage(u, pt=10) == (4, 6)
    u2 = SimpleNamespace(prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0, prompt_tokens_details=None)
    assert _extract_cache_usage(u2, pt=0) == (0, 0)


def test_find_attachment_hit_and_miss():
    files = [{"filename": "a.png", "mime_type": "image/png", "data": "x"}]
    assert _find_attachment(files, "a.png") == files[0]
    with pytest.raises(KeyError):
        _find_attachment(files, "b.png")


def test_attachment_parts_split(monkeypatch):
    calls = {}

    def fake_text(others, budget=6000):
        calls["others"] = others
        calls["budget"] = budget
        return "TXT"
    fake_mod = SimpleNamespace(attachment_context_text=fake_text)
    monkeypatch.setitem(sys.modules, "app.agent.graphmod.attachment_loader", fake_mod)
    files = [
        {"filename": "img.png", "mime_type": "image/png", "data": "x"},
        {"filename": "pic.JPG", "mime_type": "application/octet-stream", "data": "y"},  # 扩展名识别
        {"filename": "doc.pdf", "mime_type": "application/pdf", "data": "z"},
    ]
    images, text = _attachment_parts(files)
    assert images == ["img.png", "pic.JPG"]
    assert calls["others"] == [{"filename": "doc.pdf", "mime_type": "application/pdf", "data": "z"}]
    assert text == "TXT"


def test_attachment_parts_no_others(monkeypatch):
    fake_mod = SimpleNamespace(attachment_context_text=lambda others, budget=6000: "X")
    monkeypatch.setitem(sys.modules, "app.agent.graphmod.attachment_loader", fake_mod)
    images, text = _attachment_parts([])
    assert images == [] and text == ""


def test_attachment_parts_latent_import_bug():
    """已知产品缺陷锁定：graphmod 无 attachment_loader 子模块，`from . import
    attachment_loader` 无条件执行 → 任何附件请求都抛 ImportError（即使无文本附件）。
    修法：state.py 改为 `from .. import attachment_loader`。"""
    with pytest.raises(ImportError):
        _attachment_parts([])


def test_zero_usage_shape():
    assert set(_ZERO_USAGE) == {"input", "output", "reasoning", "cache_read", "cache_write"}


# ── constants.py ───────────────────────────────────────────────────────────

def test_normalize_finish_reason():
    assert _normalize_finish_reason(None) == "stop"
    assert _normalize_finish_reason("") == "stop"
    assert _normalize_finish_reason("stop") == "stop"
    assert _normalize_finish_reason("length") == "length"
    assert _normalize_finish_reason("max_tokens") == "length"
    assert _normalize_finish_reason("tool_calls") == "tool-calls"
    assert _normalize_finish_reason("function_call") == "tool-calls"
    assert _normalize_finish_reason("content_filter") == "content-filter"
    assert _normalize_finish_reason("error") == "error"
    assert _normalize_finish_reason("MAX_TOKENS") == "length"
    assert _normalize_finish_reason(" tool_calls ") == "tool-calls"
    assert _normalize_finish_reason("mystery") == "unknown"


def test_finish_reason_map_complete():
    assert _FINISH_REASON_MAP["stop"] == "stop"
    assert _FINISH_REASON_MAP["max_tokens"] == "length"


def test_nearest_workspace_hint_under_workspace(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SimpleNamespace(list_workspaces=lambda: [str(ws)])
    monkeypatch.setattr("app.permission.get_manager", lambda: mgr)
    hint = _nearest_workspace_hint(str(ws / "app" / "x.py"))
    assert "受保护的系统/源码路径" in hint


def test_nearest_workspace_hint_near_workspace(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SimpleNamespace(list_workspaces=lambda: [str(ws)])
    monkeypatch.setattr("app.permission.get_manager", lambda: mgr)
    hint = _nearest_workspace_hint(str(tmp_path))
    assert "可写工作区" in hint


def test_nearest_workspace_hint_far(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SimpleNamespace(list_workspaces=lambda: [str(ws)])
    monkeypatch.setattr("app.permission.get_manager", lambda: mgr)
    hint = _nearest_workspace_hint(str(tmp_path / "far" / "x.txt"))
    assert "不在当前可写工作区内" in hint


def test_nearest_workspace_hint_exception(monkeypatch):
    def boom():
        raise RuntimeError
    monkeypatch.setattr("app.permission.get_manager", boom)
    assert "不在当前可写工作区内" in _nearest_workspace_hint("/whatever")


def test_permission_denied_msg():
    msg = _permission_denied_msg("write", "/x/y.txt")
    assert msg.startswith("Permission denied: write on '/x/y.txt'.")
    assert "不是可重试的临时错误" in msg
    msg2 = _permission_denied_msg("execute", "/x", "tool_execute")
    assert "(tool=tool_execute)" in msg2


def test_is_multi_agent_queue():
    q = asyncio.Queue()
    assert _is_multi_agent_queue(q) is False
    from app.agent.stream_events import AgentEventCollector, TaggedEventQueue
    c = AgentEventCollector(q)
    assert _is_multi_agent_queue(c) is True
    tagged = TaggedEventQueue(c, "rag")
    assert _is_multi_agent_queue(tagged) is True


def test_constants_sets():
    assert "tool_ls" in _DEDUP_READONLY_TOOLS
    assert _TASK_TOOL_SUBAGENTS == ("web_search", "code")
    assert _TASK_TOOL_SCHEMA["function"]["name"] == "tool_task"
    assert _TASK_TOOL_SCHEMA["function"]["parameters"]["required"] == ["description", "prompt", "subagent_type"]
    assert "subagent_type" in _TASK_TOOL_SCHEMA["function"]["parameters"]["properties"]
    assert "MAXIMUM STEPS REACHED" in MAX_STEPS_PROMPT
    assert "死循环" in DOOM_LOOP_PROMPT


# ── base.py ────────────────────────────────────────────────────────────────

def test_activity_text():
    agent = build_agent()
    assert agent._activity_text({"type": "tool_start", "tool_name": "t"}) == "调用工具: t"
    assert agent._activity_text({"type": "tool_end", "tool_name": "t"}) == "完成工具: t"
    assert agent._activity_text({"type": "tool_output", "tool_name": "t", "elapsed_seconds": 3}) == "t 运行中 (3s)"
    assert agent._activity_text({"type": "tool_heartbeat", "tool_name": "t", "elapsed_seconds": 4}) == "t 运行中 (4s)"
    assert agent._activity_text({"type": "step_end", "name": "检索中"}) == "检索中"
    assert agent._activity_text({"type": "weird"}) == "weird"
    assert agent._activity_text({"type": "x", "name": ""}) == "x"


def test_push_event_steps_and_queue_and_cb():
    agent = build_agent()
    q = asyncio.Queue()
    cb_texts = []
    state = make_state(_event_queue=q, _on_activity=cb_texts.append)
    agent._push_event(state, {"type": "tool_start", "tool_name": "t"})
    assert state["steps"] == [{"type": "tool_start", "tool_name": "t"}]
    assert not q.empty()
    assert cb_texts == ["调用工具: t"]


def test_push_event_queue_error_and_cb_error():
    agent = build_agent()
    class BoomQueue:
        def put_nowait(self, ev):
            raise RuntimeError
    def boom_cb(text):
        raise ValueError
    state = make_state(_event_queue=BoomQueue(), _on_activity=boom_cb)
    agent._push_event(state, {"type": "step_start", "name": "x"})  # 不抛


def test_retrieve_empty_kb():
    agent = build_agent()
    state = make_state()
    out = asyncio.run(agent._retrieve(state))
    assert out == {"context": [], "sources": []}
    assert state["steps"][-1]["detail"] == "知识库为空"


def test_retrieve_use_vector_db_disabled():
    agent = build_agent(retriever=SimpleNamespace(is_empty=False, invoke=lambda q, k=3: []))
    state = make_state(use_vector_db=False)
    out = asyncio.run(agent._retrieve(state))
    assert out == {"context": [], "sources": []}
    assert state["steps"][-1]["detail"] == "已禁用向量检索"


def test_retrieve_with_results():
    docs = [
        ({"text": "content1", "metadata": {"document_id": "d1", "chapter_title": "第一章"}}, 0.9),
        ({"text": "content2", "metadata": {"document_id": "d2"}}, 0.7),
    ]
    agent = build_agent(retriever=SimpleNamespace(is_empty=False, invoke=lambda q, k=3: docs))
    state = make_state(use_vector_db=True, question="问题")
    out = asyncio.run(agent._retrieve(state))
    assert len(out["context"]) == 2
    assert out["sources"][0]["document_id"] == "d1"
    assert out["sources"][0]["score"] == 0.9
    assert state["steps"][-1]["detail"] == "找到 2 个相关片段"


def test_retrieve_exception_returns_empty(monkeypatch):
    def boom(q, k=3):
        raise RuntimeError("vector down")
    agent = build_agent(retriever=SimpleNamespace(is_empty=False, invoke=boom))
    state = make_state(use_vector_db=True)
    out = asyncio.run(agent._retrieve(state))
    assert out == {"context": [], "sources": []}


def test_rerank_disabled_no_context():
    agent = build_agent()
    assert asyncio.run(agent._rerank(make_state())) == {}
    assert asyncio.run(agent._rerank(make_state(context=[{"content": "c", "metadata": {}}]))) == {}


def test_rerank_active():
    reranker = SimpleNamespace(
        rerank=lambda query, documents, top_k: [
            ({"content": "c2", "metadata": {}}, 0.8),
            ({"content": "c1", "metadata": {}}, 0.6),
        ]
    )
    agent = build_agent(reranker=reranker)
    state = make_state(context=[{"content": "c1", "metadata": {}}, {"content": "c2", "metadata": {}}])
    out = asyncio.run(agent._rerank(state))
    assert [c["content"] for c in out["context"]] == ["c2", "c1"]
    assert state["steps"][-1]["detail"] == "筛选出 2 个最相关片段"


def test_system_prompt_with_kb():
    agent = build_agent()
    sp = agent._system_prompt_with_kb()
    assert "knowledge base" in sp
    assert "tool_task" in sp
    assert "LONG_CONTENT" in sp or "文件" in sp  # LONG_CONTENT_FILE_RULE 追加


def test_tool_matches_intent():
    agent = build_agent()
    t_weather = SimpleNamespace(name="plugin_weather_tool_get_weather")
    t_doc = SimpleNamespace(name="plugin_docx-generator_tool_create_docx")
    assert agent._tool_matches_intent(t_weather, "今天天气怎么样") is True
    assert agent._tool_matches_intent(t_doc, "帮我生成 word 文档") is True
    assert agent._tool_matches_intent(t_weather, "随便聊聊") is False
    assert agent._tool_matches_intent(SimpleNamespace(name="plugin_x"), "插件") is True


def test_build_tool_defs_core_and_intent():
    agent = build_agent()
    from app.agent.tools import ToolDef
    agent.tools.append(ToolDef(
        name="plugin_pdf-generator_tool_create_pdf",
        description="生成 PDF", parameters={}, fn=lambda: "",
    ))
    defs = agent._build_tool_defs("生成一个 pdf 报告")
    names = {d.get("function", {}).get("name") for d in defs}
    assert "tool_read_file" in names  # 核心常驻
    assert "tool_write_file" in names
    assert "plugin_pdf-generator_tool_create_pdf" in names  # 意图命中
    assert all(d["type"] == "function" for d in defs)


def test_build_tool_defs_pinned_and_used():
    from app.agent.tools import ToolDef
    pinned_name = "plugin_kb-export_tool_export_kb_to_docx"

    class PinnedStore:
        def pinned_tools(self):
            return {pinned_name}
    agent = build_agent(custom_tools=PinnedStore())
    agent.tools.append(ToolDef(name=pinned_name, description="导出", parameters={}, fn=lambda: ""))
    defs = agent._build_tool_defs("", used_names={"tool_grep"})
    names = {d.get("function", {}).get("name") for d in defs}
    assert pinned_name in names  # pinned 无条件挂载
    assert "tool_grep" in names  # 已使用保留


def test_pinned_tool_names_exception():
    class BadStore:
        def pinned_tools(self):
            raise RuntimeError
    agent = build_agent(custom_tools=BadStore())
    assert agent._pinned_tool_names() == set()
    agent2 = build_agent()
    assert agent2._pinned_tool_names() == set()


def test_bound_plugin_result_unwrap_and_weather(monkeypatch):
    agent = build_agent()
    # 信封解包
    monkeypatch.setattr(
        "app.tools.file_tools.unwrap",
        lambda r: r.get("output") if isinstance(r, dict) else r,
    )
    assert agent._bound_plugin_result("tool_x", {"title": "t", "output": "DATA"}) == "DATA"
    long_text = "x" * 2000
    marker = "\n…[已截断：天气/台风数据过长，仅保留前 1500 字符]"
    res = agent._bound_plugin_result("plugin_weather_tool_get_weather", long_text)
    assert res == "x" * 1500 + marker
    assert len(res) == 1500 + len(marker)
    assert agent._bound_plugin_result("tool_x", "plain") == "plain"