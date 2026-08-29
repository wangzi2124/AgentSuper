# -*- coding: utf-8 -*-
"""web_search_agent / code_agent 全量用例（mock 搜索/LLM，无真实网络）。

覆盖：
  - WebSearchAgent：chat/search/未知动作、异常兜底、记忆读写（namespace 隔离）、
    Tavily/DuckDuckGo 选择与回退、HTML 解析、_attachment_text、_synthesize 回退
  - CodeAgent：chat 委派主 Agent、review/explain、未知动作、异常兜底、_notify、
    _ask_llm
运行：pytest tests/test_sub_agents.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.agent.code_agent as ca
import app.agent.web_search_agent as wsa
from app.agent.base import AgentMessage
from app.agent.memory import MemoryManager


def _msg(action="chat", payload=None, type="request", thread="t1"):
    return AgentMessage(source="supervisor", target="web_search", type=type,
                        action=action, payload=payload or {}, thread_id=thread)


async def _collect(agent, msg):
    return [r async for r in agent.handle_message(msg)]


class FakeMemory(MemoryManager):
    def __init__(self, tmp_path):
        super().__init__(persist_path=str(tmp_path / "m.json"))
        self.sets = []
        self.gets = []

    async def get(self, key, default=None, namespace=""):
        self.gets.append((key, namespace))
        return await super().get(key, default, namespace)

    async def set(self, key, value, ttl=None, tags=None, namespace=""):
        self.sets.append((key, namespace, tags))
        return await super().set(key, value, ttl, tags, namespace)


# ── WebSearchAgent ─────────────────────────────────────────────────────────

def _ws(memory=None):
    return wsa.WebSearchAgent(memory=memory)


@pytest.mark.asyncio
async def test_ws_non_request_returns_nothing():
    agent = _ws()
    replies = await _collect(agent, _msg(type="response"))
    assert replies == []


@pytest.mark.asyncio
async def test_ws_chat_action(monkeypatch, tmp_path):
    mm = FakeMemory(tmp_path)
    agent = _ws(memory=mm)

    async def fake_search(q, max_results):
        return [{"title": "标题", "url": "https://x", "snippet": "摘要"}]
    monkeypatch.setattr(agent, "_search_web", fake_search)

    async def fake_synth(*a, **k):
        return "合成答案"
    monkeypatch.setattr(agent, "_synthesize", fake_synth)
    q = asyncio.Queue()
    replies = await _collect(agent, _msg(payload={"question": "q", "conversation_id": "cid", "_event_queue": q}))
    assert len(replies) == 1
    assert replies[0].type == "response"
    assert replies[0].payload["answer"] == "合成答案"
    assert replies[0].payload["sources"][0]["document_id"] == "https://x"
    # 记忆读写按 conversation 隔离
    assert mm.gets[-1] == ("last_search_results", "cid")
    assert mm.sets[-1][1] == "cid"
    # 事件已发
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "agent_start" in types and "agent_done" in types


@pytest.mark.asyncio
async def test_ws_search_action(monkeypatch):
    agent = _ws()

    async def fake_search(q, max_results):
        return [{"title": "T", "url": "http://u", "snippet": "S"}]
    monkeypatch.setattr(agent, "_search_web", fake_search)
    replies = await _collect(agent, _msg(action="search", payload={"query": "x", "max_results": 3}))
    assert replies[0].payload["results"] == [{"title": "T", "url": "http://u", "snippet": "S"}]
    assert "[T](http://u)" in replies[0].payload["answer"]


@pytest.mark.asyncio
async def test_ws_unknown_action():
    agent = _ws()
    replies = await _collect(agent, _msg(action="bogus"))
    assert replies[0].type == "error"
    assert "Unknown action" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_ws_exception_handled(monkeypatch):
    agent = _ws()

    async def boom(q, max_results):
        raise RuntimeError("search down")
    monkeypatch.setattr(agent, "_search_web", boom)
    q = asyncio.Queue()
    replies = await _collect(agent, _msg(payload={"_event_queue": q}))
    assert replies[0].type == "error"
    assert "search down" in replies[0].payload["error"]
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "agent_error" in types


@pytest.mark.asyncio
async def test_search_web_prefers_tavily(monkeypatch):
    agent = _ws()
    monkeypatch.setenv("TAVILY_API_KEY", "abc")
    calls = []

    async def tavily(q, n, k):
        calls.append((q, n, k))
        return ["T"]
    monkeypatch.setattr(agent, "_search_tavily", tavily)
    result = await agent._search_web("q", 5)
    assert result == ["T"]
    assert calls == [("q", 5, "abc")]


@pytest.mark.asyncio
async def test_search_web_tavily_fallback_ddg(monkeypatch):
    agent = _ws()
    monkeypatch.setenv("TAVILY_API_KEY", "abc")
    async def bad_tavily(q, n, k):
        raise RuntimeError("tavily down")
    monkeypatch.setattr(agent, "_search_tavily", bad_tavily)
    async def ddg(q, n):
        return ["D"]
    monkeypatch.setattr(agent, "_search_duckduckgo", ddg)
    assert await agent._search_web("q", 5) == ["D"]


@pytest.mark.asyncio
async def test_search_web_no_key_ddg(monkeypatch):
    agent = _ws()
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    async def ddg(q, n):
        return ["D"]
    monkeypatch.setattr(agent, "_search_duckduckgo", ddg)
    assert await agent._search_web("q", 5) == ["D"]


@pytest.mark.asyncio
async def test_search_web_all_fail_empty(monkeypatch):
    agent = _ws()
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    async def bad_ddg(q, n):
        raise RuntimeError("ddg down")
    monkeypatch.setattr(agent, "_search_duckduckgo", bad_ddg)
    assert await agent._search_web("q", 5) == []


@pytest.mark.asyncio
async def test_search_tavily(monkeypatch):
    import aiohttp
    agent = _ws()
    class FakeResp:
        async def json(self):
            return {"results": [{"title": "T", "url": "U", "content": "C"}]}
    class FakeRespCM:
        async def __aenter__(self):
            return FakeResp()
        async def __aexit__(self, *a):
            return False
    class FakeSession:
        def __init__(self):
            self.passed = {}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def post(self, url, json=None, timeout=None):
            self.passed.update(url=url, json=json)
            return FakeRespCM()

    fake = FakeSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: fake)
    results = await agent._search_tavily("q", 3, "key")
    assert results == [{"title": "T", "url": "U", "snippet": "C"}]
    assert fake.passed["json"]["api_key"] == "key"


@pytest.mark.asyncio
async def test_search_duckduckgo_parse(monkeypatch):
    import aiohttp
    agent = _ws()
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage">标题 <b>粗体</b></a>
      <a class="result__snippet">第一段 摘要</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://direct.example/x">直接链接</a>
    </div>
    """
    class FakeResp:
        async def text(self, errors="ignore"):
            return html
    class FakeRespCM:
        async def __aenter__(self):
            return FakeResp()
        async def __aexit__(self, *a):
            return False
    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def post(self, url, data=None, headers=None, timeout=None):
            return FakeRespCM()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: FakeSession())
    results = await agent._search_duckduckgo("q", 5)
    assert results[0]["url"] == "https://example.com/page"
    assert results[0]["title"] == "标题 粗体"
    assert results[0]["snippet"] == "第一段 摘要"
    assert results[1]["url"] == "https://direct.example/x"


@pytest.mark.asyncio
async def test_attachment_text_empty_and_with_files(monkeypatch):
    import app.agent.image_processor as ip_mod
    assert await wsa.WebSearchAgent._attachment_text([]) == ""
    async def fake_ctx(files, budget=3000):
        return "正文"
    monkeypatch.setattr(ip_mod, "attachment_context_with_images", fake_ctx)
    out = await wsa.WebSearchAgent._attachment_text([{"filename": "a.txt", "mime_type": "text/plain", "data": "x"}])
    assert "用户附带的文档/图片内容" in out and "正文" in out


@pytest.mark.asyncio
async def test_synthesize_no_results():
    agent = _ws()
    assert await agent._synthesize("q", []) == "抱歉，我没有找到相关的信息。"


@pytest.mark.asyncio
async def test_synthesize_uses_tool_loop(monkeypatch):
    agent = _ws()

    async def fake_loop(**kw):
        return "LLM 答案"
    monkeypatch.setattr(wsa, "tool_loop_chat", fake_loop)
    out = await agent._synthesize("q", [{"title": "T", "url": "U", "snippet": "S"}])
    assert out == "LLM 答案"


@pytest.mark.asyncio
async def test_synthesize_fallback_on_llm_error(monkeypatch):
    agent = _ws()
    async def boom(**kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(wsa, "tool_loop_chat", boom)
    out = await agent._synthesize("q", [{"title": "T", "url": "U", "snippet": "S"}])
    assert "搜索结果" in out and "T" in out


# ── CodeAgent ──────────────────────────────────────────────────────────────

def _code(inner=None, memory=None, heartbeat=None):
    return ca.CodeAgent(inner=inner or SimpleNamespace(), memory=memory, heartbeat=heartbeat)


class FakeInner:
    def __init__(self, answer="A", sources=None):
        self.answer = answer
        self.sources = sources or []
        self.calls = []

    async def invoke(self, **kw):
        self.calls.append(kw)
        return {"answer": self.answer, "sources": self.sources, "steps": [], "tokens": {"input": 1}}


@pytest.mark.asyncio
async def test_code_chat_delegates_inner(monkeypatch, tmp_path):
    inner = FakeInner()
    mm = FakeMemory(tmp_path)
    agent = _code(inner=inner, memory=mm)
    q = asyncio.Queue()
    replies = await _collect(agent, _msg(payload={
        "question": "实现 x", "conversation_id": "cid", "history": [{"role": "user", "content": "h"}],
        "directory": "/w", "_task_depth": 1, "_event_queue": q,
    }))
    assert replies[0].type == "response"
    assert replies[0].payload["answer"] == "A"
    assert replies[0].payload["tokens"] == {"input": 1}
    kw = inner.calls[0]
    assert kw["question"] == "实现 x"
    assert kw["conversation_id"] == "cid"
    assert kw["directory"] == "/w"
    assert kw["task_depth"] == 1
    assert mm.sets and mm.sets[0][1] == "cid"  # 记忆按 conversation 隔离
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert "agent_start" in types and "agent_done" in types


@pytest.mark.asyncio
async def test_code_review_and_explain(monkeypatch):
    agent = _code()
    calls = []

    async def fake_ask(**kw):
        calls.append(kw["user_message"])
        return "R"
    monkeypatch.setattr(agent, "_ask_llm", fake_ask)
    replies = await _collect(agent, _msg(action="review", payload={"code": "x=1", "language": "python"}))
    assert replies[0].payload["answer"] == "R"
    replies = await _collect(agent, _msg(action="explain", payload={"code": "x=1"}))
    assert replies[0].payload["answer"] == "R"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_code_unknown_action():
    agent = _code()
    replies = await _collect(agent, _msg(action="nope"))
    assert replies[0].type == "error"


@pytest.mark.asyncio
async def test_code_exception_handled(monkeypatch):
    class BoomInner:
        async def invoke(self, **kw):
            raise RuntimeError("inner boom")
    agent = _code(inner=BoomInner())
    q = asyncio.Queue()
    replies = await _collect(agent, _msg(payload={"_event_queue": q}))
    assert replies[0].type == "error"
    assert "inner boom" in replies[0].payload["error"]


@pytest.mark.asyncio
async def test_code_non_request_returns_nothing():
    agent = _code()
    assert await _collect(agent, _msg(type="response")) == []


def test_notify_heartbeat():
    calls = []
    agent = _code(heartbeat=lambda aid, prog: calls.append((aid, prog)))
    agent._notify("进度")
    assert calls == [("code", "进度")]


def test_notify_heartbeat_error_swallowed():
    def boom(aid, prog):
        raise RuntimeError
    agent = _code(heartbeat=boom)
    agent._notify("进度")  # 不抛


@pytest.mark.asyncio
async def test_ask_llm(monkeypatch, tmp_path):
    import app.agent.code_agent as ca_mod
    agent = _code()
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        choices=[SimpleNamespace(message=SimpleNamespace(content="  回答内容  "))],
    )
    calls = []
    async def fake_acompletion(**kw):
        calls.append(kw)
        return resp
    monkeypatch.setattr(ca_mod.litellm, "acompletion", fake_acompletion)
    rec = []
    monkeypatch.setattr(ca_mod, "record_model_call", lambda *a, **k: rec.append(a))
    out = await agent._ask_llm("sys", "user")
    assert out == "回答内容"
    assert calls[0]["max_tokens"] == 2048
    assert rec