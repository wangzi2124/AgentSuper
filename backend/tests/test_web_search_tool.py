# -*- coding: utf-8 -*-
"""web_search_tool（live，build agent 内建工具）+ WebSearchAgent 引擎链测试。

web_search_tool 由 graphmod/base.py:132 注册给 build agent，是唯一存活的
web 搜索入口（独立 web_search 子 Agent 路由已随多 Agent 合并移除）。本文件只
验工具输出格式化与 Tavily→DuckDuckGo 引擎链回退，不做真实网络请求。

（原 test_sub_agents.py 随 dead CodeAgent/长任务路由一并删除，其 WebSearchAgent
行为覆盖只剩引擎链——chat 动作 LLM 合成走 tool_loop_chat 已在 test_sub_tools 覆盖。）
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent import web_search_agent as wsa


SAMPLE = [
    {"title": "T1", "url": "https://u1", "snippet": "s1"},
    {"title": "T2", "url": "https://u2", "snippet": "s2"},
]


class _FakeAgent:
    def __init__(self, impl):
        self._impl = impl

    async def _search_web(self, q, max_results):
        return await self._impl(q, max_results)


# ── web_search_tool（live 工具）────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_tool_formats_results(monkeypatch):
    async def fake_search(q, max_results, **kw):
        return SAMPLE
    monkeypatch.setattr(wsa, "_search_agent", lambda: _FakeAgent(fake_search))
    out = await wsa.web_search_tool("q", 2)
    assert "1. T1" in out and "https://u1" in out
    assert "2. T2" in out and "s2" in out


@pytest.mark.asyncio
async def test_web_search_tool_empty_placeholder(monkeypatch):
    async def fake_search(q, max_results, **kw):
        return []
    monkeypatch.setattr(wsa, "_search_agent", lambda: _FakeAgent(fake_search))
    assert await wsa.web_search_tool("nothing") == "未找到相关的实时信息。"


@pytest.mark.asyncio
async def test_web_search_tool_error_placeholder(monkeypatch):
    async def boom(q, max_results, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(wsa, "_search_agent", lambda: _FakeAgent(boom))
    out = await wsa.web_search_tool("q")
    assert out.startswith("Error: 网络搜索失败")


@pytest.mark.asyncio
async def test_web_search_tool_clamps_max_results(monkeypatch):
    seen = {}

    async def fake_search(q, max_results, **kw):
        seen["n"] = max_results
        return SAMPLE
    monkeypatch.setattr(wsa, "_search_agent", lambda: _FakeAgent(fake_search))
    await wsa.web_search_tool("q", -3)  # 负数 → 钳到 1
    assert seen["n"] == 1
    await wsa.web_search_tool("q", 0)   # 0/None → 默认 5
    assert seen["n"] == 5
    await wsa.web_search_tool("q", 99)  # 超上限 → 钳到 10
    assert seen["n"] == 10


# ── WebSearchAgent 引擎链（Tavily → DuckDuckGo 回退）──────────────────────

@pytest.mark.asyncio
async def test_search_web_tavily_fallback_to_ddg(monkeypatch):
    agent = wsa.WebSearchAgent()
    seen = {}

    async def tavily(q, n, key):
        raise RuntimeError("tavily down")

    async def ddg(q, n):
        seen["ddg"] = True
        return SAMPLE
    monkeypatch.setattr(agent, "_search_tavily", tavily)
    monkeypatch.setattr(agent, "_search_duckduckgo", ddg)
    monkeypatch.setenv("TAVILY_API_KEY", "xyz")
    out = await agent._search_web("q", 3)
    assert out == SAMPLE and seen.get("ddg")


@pytest.mark.asyncio
async def test_search_web_no_key_uses_ddg(monkeypatch):
    agent = wsa.WebSearchAgent()
    seen = {}

    async def ddg(q, n):
        seen["ddg"] = True
        return SAMPLE
    monkeypatch.setattr(agent, "_search_duckduckgo", ddg)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    out = await agent._search_web("q", 3)
    assert out == SAMPLE and seen.get("ddg")


@pytest.mark.asyncio
async def test_search_web_ddg_failure_returns_empty(monkeypatch):
    agent = wsa.WebSearchAgent()

    async def ddg(q, n):
        raise RuntimeError("ddg down")
    monkeypatch.setattr(agent, "_search_duckduckgo", ddg)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert await agent._search_web("q", 3) == []