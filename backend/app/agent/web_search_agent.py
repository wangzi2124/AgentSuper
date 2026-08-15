"""WebSearchAgent — 网络搜索 Agent。

处理需要实时/外部信息的查询。使用内置的互联网搜索能力（通过插件接口）
获取最新信息，然后使用 LLM 组织回答。

支持的动作:
  - "chat":     搜索 + LLM 生成回答
  - "search":   仅搜索（返回原始结果）
"""

import asyncio
import logging
import json
import time as tmod
from typing import AsyncIterator, Optional

from app.agent.base import BaseAgent, AgentMessage
from app.agent.memory import MemoryManager
from app.agent.stream_events import agent_meta, emit, step_event
from app.agent.sub_tools import tool_loop_chat
from app.config import settings

logger = logging.getLogger(__name__)

_SEARCH_ENGINES = {
    "baidu": "百度 (适合中文内容)",
    "duckduckgo": "DuckDuckGo (国际内容，无需API Key)",
    "tavily": "Tavily (国际内容，需 API Key)",
}


class WebSearchAgent(BaseAgent):
    """网络搜索 Agent。

    使用互联网搜索获取最新信息，结合 LLM 生成回答。
    适合处理需要实时数据、最新新闻、或不在知识库中的问题。
    """

    def __init__(
        self,
        memory: Optional[MemoryManager] = None,
        agent_id: str = "web_search",
    ):
        self._id = agent_id
        self._memory = memory
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base

    @property
    def agent_id(self) -> str:
        return self._id

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            return

        action = msg.action
        payload = msg.payload

        try:
            if action == "chat":
                # 搜索 + LLM 合成回答
                question = payload.get("question", "")
                max_results = payload.get("max_results", 5)
                conv_id = payload.get("conversation_id", "")
                event_queue = payload.get("_event_queue")
                name, avatar = agent_meta(self._id)
                emit(event_queue, {
                    "type": "agent_start",
                    "agent_id": self._id,
                    "agent_name": name,
                    "agent_avatar": avatar,
                })

                # 步骤 1: 搜索
                start = tmod.time()
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event("search", "搜索网络", "running"),
                })
                search_results = await self._search_web(question, max_results)
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event(
                        "search", "搜索网络", "completed",
                        detail=f"找到 {len(search_results)} 条结果",
                        duration_ms=(tmod.time() - start) * 1000,
                    ),
                })

                # 步骤 2: 检查记忆中有无相关上下文（按 conversation 隔离）
                memory_context = ""
                if self._memory:
                    previous_searches = await self._memory.get(
                        "last_search_results",
                        namespace=conv_id,  # 🔒 Session 隔离
                    )
                    if previous_searches:
                        memory_context = f"\n[之前的搜索结果]:\n{previous_searches[:500]}\n"

                # 步骤 3: LLM 合成
                start = tmod.time()
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event("synthesize", "合成回答", "running"),
                })
                answer = await self._synthesize(
                    question, search_results, memory_context,
                    event_queue=event_queue, agent_id=self._id,
                    history=payload.get("history") or [],
                    directory=payload.get("directory", ""),
                )
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event(
                        "synthesize", "合成回答", "completed",
                        duration_ms=(tmod.time() - start) * 1000,
                    ),
                })

                # 步骤 4: 存入记忆（按 conversation 隔离）
                if self._memory:
                    await self._memory.set(
                        "last_search_results",
                        f"Q: {question}\nA: {answer[:300]}...",
                        ttl=120,
                        tags=["web_search"],
                        namespace=conv_id,  # 🔒 Session 隔离
                    )

                emit(event_queue, {
                    "type": "agent_done",
                    "agent_id": self._id,
                    "content": answer,
                })
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="chat",
                    payload={
                        "answer": answer,
                        "sources": [
                            {
                                "document_id": r.get("url", ""),
                                "content": r.get("snippet", ""),
                                "score": 1.0 - (i * 0.05),
                            }
                            for i, r in enumerate(search_results[:5])
                        ] if search_results else [],
                        "steps": [],
                    },
                    thread_id=msg.thread_id,
                )

            elif action == "search":
                # 仅搜索
                question = payload.get("query", "")
                max_results = payload.get("max_results", 5)
                results = await self._search_web(question, max_results)

                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="search",
                    payload={
                        "results": results,
                        "answer": "\n\n".join(
                            f"{i+1}. [{r.get('title','')}]({r.get('url','')})\n   {r.get('snippet','')}"
                            for i, r in enumerate(results[:10])
                        ),
                    },
                    thread_id=msg.thread_id,
                )

            else:
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="error", action=action,
                    payload={"error": f"Unknown action: {action}"},
                    thread_id=msg.thread_id,
                )

        except Exception as e:
            logger.exception("WebSearchAgent error on action=%s", action)
            emit(payload.get("_event_queue"), {
                "type": "agent_error",
                "agent_id": self._id,
                "error": str(e),
            })
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": str(e)},
                thread_id=msg.thread_id,
            )

    async def _search_web(self, query: str, max_results: int = 5) -> list[dict]:
        """使用插件系统搜索网络。

        优先使用 Tavily（如果有 API Key），否则回退到 DuckDuckGo。
        """
        # 尝试 Tavily
        import os
        tavily_key = os.environ.get("TAVILY_API_KEY", "")
        if tavily_key and tavily_key not in ("", "your-tavily-api-key"):
            try:
                return await self._search_tavily(query, max_results, tavily_key)
            except Exception as e:
                logger.warning("Tavily search failed, falling back to DuckDuckGo: %s", e)

        # 回退到 DuckDuckGo
        try:
            return await self._search_duckduckgo(query, max_results)
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)
            return []

    async def _search_tavily(self, query: str, max_results: int, api_key: str) -> list[dict]:
        """使用 Tavily API 搜索。"""
        import aiohttp

        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                results = data.get("results", [])
                logger.info("Tavily returned %d results for '%s'", len(results), query[:50])
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("content", ""),
                    }
                    for r in results
                ]

    async def _search_duckduckgo(self, query: str, max_results: int) -> list[dict]:
        """使用 DuckDuckGo HTML 端点搜索（无需 API Key，返回通用网页结果）。"""
        import aiohttp
        from urllib.parse import urlencode, unquote

        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        data = urlencode({"q": query})

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text(errors="ignore")

        import re
        results = []
        blocks = re.split(r'<div[^>]*class="result(?: |")', html)[1:]

        for block in blocks:
            title_match = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not title_match:
                continue

            link = title_match.group(1)
            redirect = re.search(r'uddg=([^&]+)', link)
            if redirect:
                link = unquote(redirect.group(1))
            elif link.startswith("//"):
                link = "https:" + link

            title = re.sub(r"<[^>]+>", "", title_match.group(2)).strip()

            snippet = ""
            snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

            results.append({"title": title, "url": link, "snippet": snippet})
            if len(results) >= max_results:
                break

        logger.info("DuckDuckGo returned %d results for '%s'", len(results), query[:50])
        return results[:max_results]

    async def _synthesize(
        self,
        question: str,
        search_results: list[dict],
        memory_context: str = "",
        event_queue=None,
        agent_id: str = "",
        history: Optional[list[dict]] = None,
        directory: str = "",
    ) -> str:
        """使用 LLM 将搜索结果合成为回答（带文件工具，可按需核对工作区内容）。"""
        if not search_results:
            return "抱歉，我没有找到相关的信息。"

        sources_text = "\n\n".join(
            f"[来源 {i+1}] {r.get('title','')}\n{r.get('snippet','')}\nURL: {r.get('url','')}"
            for i, r in enumerate(search_results[:8])
        )

        system_prompt = """你是一个网络搜索助手。根据以下搜索结果，用中文回答用户的问题。

要求:
- 用自然的语言组织信息，不要直接罗列搜索结果
- 在回答末尾列出信息来源编号
- 如果搜索结果不足，诚实说明
- 使用中文回答
- 必要时可用文件工具读写工作区文件（如保存搜索结果、核对项目内容）"""

        user_prompt = f"""用户问题: {question}

{memory_context}

搜索结果:
{sources_text}

请基于以上信息回答用户问题。"""

        try:
            return await tool_loop_chat(
                system_prompt=system_prompt,
                user_message=user_prompt,
                event_queue=event_queue,
                agent_id=agent_id,
                history=history,
                directory=directory,
            )
        except Exception as e:
            logger.error("LLM synthesis failed: %s", e)
            # 回退：直接返回搜索结果摘要
            lines = [f"搜索结果:"] + [
                f"- {r.get('title','')}: {r.get('snippet','')[:100]}"
                for r in search_results[:5]
            ]
            return "\n".join(lines)
