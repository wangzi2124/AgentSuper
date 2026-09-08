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
        """将请求路由到合适的 Agent。

        [opencode build 合并] rag/code/web_search 已合并为单一 build agent
        （知识库 + 代码/文件 + 内建 web 搜索），故不再按 kb/code/web 关键词拆分；
        除明确的"只读探索"意图走 explore 外，其余统一由 build 处理。
        返回格式: [{"agent": "build" | "explore" | "plan", "question": "..."}]
        """
        q = question.strip().lower()

        # ── 快速路径: 关键词 + 可用 Agent 判断 ──
        available_agents = [a for a in self._bus.list_agents() if a in self.ROUTABLE_AGENTS]

        # 明确的"只读探索代码库"意图 → explore（其余代码/文档/网络问题都由 build 覆盖）
        explore_keywords = [
            "代码库", "目录结构", "项目结构", "源码结构", "文件结构", "工作区结构",
            "哪个文件", "文件在哪", "这个项目", "源代码在哪", "函数定义在哪", "类定义在哪",
            "仓库结构", "整个项目", "有哪些文件", "项目里",
            "structure of", "where is the file", "files in",
        ]
        needs_explore = any(kw in q for kw in explore_keywords) and "explore" in available_agents

        if needs_explore:
            return [{"agent": "explore", "question": question}]

        # ── [B6] 简短寒暄直接走 build 免 LLM ──
        if len(q) <= 24 and self._is_greeting(q):
            return [{"agent": "build", "question": question}]

        # ── 其余情况：build 全能力覆盖，直接路由，不再逐请求 LLM 拆分 ──
        return [{"agent": "build", "question": question}]
    async def _llm_decompose(self, question: str, available: list[str]) -> list[dict]:
        """使用 LLM 判断如何分解任务。

        - 输出先做 JSON 解析 + schema 校验（agent 必须在白名单且可用、question 非空）
        - 解析/校验失败时带错误信息与格式样例做一次 few-shot 修复重试
        - 仍失败才回退 build（记录原因，便于排查路由漂移）
        """
        routable = [a for a in available if a in self.ROUTABLE_AGENTS] or ["build"]

        async def _request(messages: list[dict]) -> tuple[str, dict]:
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                api_base=self._api_base,
                messages=messages,
                max_tokens=1024,
                temperature=0.1,
                cache_prompt=True,
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
        for attempt in range(2):  # [token 优化] 首次 + 1 次 few-shot 修复重试，仍失败才回退 build
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
                                '[\n  {"agent": "build", "question": "第一个子任务的问题描述"},\n'
                                '  {"agent": "explore", "question": "第二个子任务的问题描述"}\n]\n'
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
            "LLM decomposition failed after %d attempt(s): %s; falling back to build",
            len(attempts), attempts[-1] if attempts else "unknown",
        )
        return [{"agent": "build", "question": question}]
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
