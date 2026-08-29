"""拆分模块 `decompose`（含 SupervisorAgentDecompose）。

原文件 docstring: Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 支持任务分解：将复杂问题拆成多个子任务并行执行
  4. 通过 AgentBus 转发请求并等待回复
  5. 将子 Agent 的回答包装后返回给用户

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future"""
# ── 复制自原模块的顶层 import ──
import asyncio

import logging

import re

import time as tmod

import uuid

from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage

from app.agent.bus import AgentBus

from app.agent.memory import MemoryManager

from app.config import settings

from app.monitor import record_model_call

from app.utils.json_repair import parse_json_value
from .core import SupervisorAgentCore
# ── 跨子模块依赖（自动生成）──
from .constants import DECOMPOSE_SYSTEM_PROMPT
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class SupervisorAgentDecompose(SupervisorAgentCore):
    def _is_greeting(self, q: str) -> bool:
        """[B6] 判断是否为简短的寒暄/闲聊（用于 ≤24 字符快速路径）。"""
        return any(k in q for k in self._GREETING_KEYWORDS)
    async def _decompose(self, question: str) -> list[dict]:
        """将复杂问题拆解成多个子任务。

        返回格式: [{"agent": "rag", "question": "..."}, ...]
        """
        q = question.strip().lower()

        # ── 快速路径: 关键词 + 可用 Agent 判断 ──
        available_agents = [a for a in self._bus.list_agents() if a in self.ROUTABLE_AGENTS]

        kb_keywords = [
            "文档", "小说", "角色", "对话", "章节", "故事", "内容", "知识库",
            "人物", "情节", "书中", "记载", "来源", "character", "dialogue",
            "novel", "chapter", "story",
            # [token 优化] 扩充
            "摘要", "总结", "作者", "主角", "配角", "人物关系", "出场", "设定",
            "世界观", "结局", "大意", "简介", "summary", "author", "plot",
        ]
        code_keywords = [
            "代码", "编程", "函数", "bug", "debug", "程序", "算法",
            "python", "javascript", "typescript", "前端", "后端",
            "code", "function", "programming",
            # [token 优化] 扩充
            "脚本", "接口", "api", "报错", "异常", "重构", "依赖", "配置",
            "测试", "部署", "数据库", "sql", "react", "vue", "node", "docker", "git",
        ]
        web_keywords = [
            "新闻", "最新", "天气", "搜索", "查找", "实时",
            "news", "weather", "search", "latest", "today",
            # [token 优化] 扩充
            "热搜", "公告", "发布", "汇率", "股价", "比赛", "比分", "排行榜",
            "政策", "法规", "通知", "announcement", "release",
        ]

        needs_kb = any(kw in q for kw in kb_keywords) and "rag" in available_agents
        needs_code = any(kw in q for kw in code_keywords) and "code" in available_agents
        needs_web = any(kw in q for kw in web_keywords) and "web_search" in available_agents

        # 如果关键词匹配到多个，尝试 LLM 分解
        if (needs_kb and needs_code) or (needs_kb and needs_web) or (needs_code and needs_web):
            return await self._llm_decompose(question, available_agents)

        # 单一明确意图
        if needs_code:
            return [{"agent": "code", "question": question}]
        if needs_web:
            return [{"agent": "web_search", "question": question}]
        if needs_kb:
            return [{"agent": "rag", "question": question}]

        # ── [B6] 简短问题(≤24字符)：寒暄直接走 rag 免 LLM；
        #        非寒暄且零关键词命中则强制 LLM 分解，避免简单但明确的
        #        请求(如"帮我写个爬虫")被盲目路由到 rag。──
        if len(q) <= 24:
            if self._is_greeting(q):
                return [{"agent": "rag", "question": question}]
            return await self._llm_decompose(question, available_agents)

        # ── 默认: 尝试 LLM 分解 ──
        return await self._llm_decompose(question, available_agents)
    async def _llm_decompose(self, question: str, available: list[str]) -> list[dict]:
        """使用 LLM 判断如何分解任务。

        - 输出先做 JSON 解析 + schema 校验（agent 必须在白名单且可用、question 非空）
        - 解析/校验失败时带错误信息与格式样例做一次 few-shot 修复重试
        - 仍失败才回退 rag（记录原因，便于排查路由漂移）
        """
        routable = [a for a in available if a in self.ROUTABLE_AGENTS] or ["rag"]

        async def _request(messages: list[dict]) -> tuple[str, dict]:
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                api_base=self._api_base,
                messages=messages,
                max_tokens=1024,
                temperature=0.1,
            )
            usage = getattr(response, "usage", None)
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            }
            # [token 优化 v9] 分解调用的用量计入本次请求汇总
            if getattr(self, "_usage", None) is not None:
                self._usage["input"] += usage_dict.get("prompt_tokens", 0)
                self._usage["output"] += usage_dict.get("completion_tokens", 0)
            return response.choices[0].message.content, usage_dict

        start = tmod.time()
        attempts = []
        for attempt in range(2):  # [token 优化] 首次 + 1 次 few-shot 修复重试，仍失败才回退 rag
            try:
                if attempt == 0:
                    messages = [
                        {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                        {"role": "user", "content": f"可用的 Agent: {', '.join(routable)}\n\n用户问题: {question}"},
                    ]
                else:
                    # few-shot 修复：带上一次的错误与合法格式样例
                    messages = [
                        {"role": "system", "content": DECOMPOSE_SYSTEM_PROMPT},
                        {"role": "user", "content": f"可用的 Agent: {', '.join(routable)}\n\n用户问题: {question}"},
                        {
                            "role": "assistant",
                            "content": "抱歉，我需要先输出子任务分解。",
                        },
                        {
                            "role": "user",
                            "content": (
                                "你上一次的输出无法解析，原因如下：\n"
                                f"{attempts[-1]}\n\n"
                                "请严格按照以下 JSON 数组格式重新输出（不要 markdown 代码块标记），"
                                "且 agent 字段只能取 " + ", ".join(routable) + "：\n"
                                '[\n  {"agent": "rag", "question": "第一个子任务的问题描述"},\n'
                                '  {"agent": "web_search", "question": "第二个子任务的问题描述"}\n]\n'
                            ),
                        },
                    ]
                text, usage = await _request(messages)
                if attempt == 0:
                    dur = (tmod.time() - start) * 1000
                    record_model_call(
                        self._model,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        duration_ms=dur,
                    )
                text = text.strip()
                text = text.replace("```json", "").replace("```", "").strip()
                subtasks = parse_json_value(text)
                validated = self._validate_subtasks(subtasks, routable)
                if validated:
                    return validated
                attempts.append("schema 校验未通过：返回了空/非法的子任务列表")
            except Exception as e:  # noqa: BLE001
                attempts.append(f"{type(e).__name__}: {e}")

        logger.warning(
            "LLM decomposition failed after %d attempt(s): %s; falling back to rag",
            len(attempts), attempts[-1] if attempts else "unknown",
        )
        return [{"agent": "rag", "question": question}]
    @staticmethod
    def _validate_subtasks(data, routable: list[str]) -> list[dict]:
        """校验并规范化 LLM 分解输出，返回合法子任务列表（白名单过滤 + 最多 3 个）。"""
        if not isinstance(data, list):
            return []
        validated: list[dict] = []
        for st in data:
            if not isinstance(st, dict):
                continue
            agent = st.get("agent")
            q = st.get("question")
            if not isinstance(agent, str) or not isinstance(q, str) or not q.strip():
                continue
            if agent in routable:
                validated.append({"agent": agent, "question": q.strip()})
            if len(validated) >= 3:
                break
        return validated

__all__ = ['SupervisorAgentDecompose']
