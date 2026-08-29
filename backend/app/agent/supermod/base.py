"""拆分模块 `base`（含 SupervisorAgentBase）。

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
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class SupervisorAgentBase(BaseAgent):

    # 可路由的 Agent 白名单（排除 supervisor 自身，防止 LLM 返回 "supervisor" 造成自我递归）
    ROUTABLE_AGENTS = {"rag", "web_search", "code"}
    def __init__(self, bus: AgentBus, memory: Optional[MemoryManager] = None):
        self._bus = bus
        self._memory = memory
        self._id = "supervisor"
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key
        self._api_base = settings.llm_api_base
        # 使用更长超时的子 Agent（工具密集型，如 code）
        self._extended_timeout_agents = {
            a.strip() for a in (settings.extended_timeout_agents or "").split(",") if a.strip()
        }
    def _timeout_for(self, agent_id: str) -> float:
        """按 Agent 类型分级超时：工具密集型 Agent 使用更长等待，避免长任务被误判超时。"""
        if agent_id in self._extended_timeout_agents:
            return settings.sub_agent_timeout_extended
        return settings.sub_agent_timeout
    @property
    def agent_id(self) -> str:
        return self._id
    def _start_heartbeat(self, interval: float = 5.0):
        """[A2] 启动 supervisor 自身的心跳任务：处理期间周期 touch，避免上层超时误判。

        supervisor 的 LLM 分解 / 等待子 Agent / 汇总都可能持续数秒到数分钟，
        期间其事件循环阻塞在 handle_message 内、bus 无法自动 touch——因此这里
        主动周期 touch，让 endpoint 侧 send_and_wait 的 grace 续期逻辑能看到
        "supervisor 仍在处理"，而非把它当死任务提前超时。
        Returns:
            心跳 task（调用方需在收尾时 cancel）；失败返回 None。
        """
        try:
            async def _beat():
                try:
                    while True:
                        self._bus.touch(self._id)
                        await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    pass
            return asyncio.create_task(_beat())
        except Exception as e:  # noqa: BLE001
            logger.warning("Supervisor heartbeat failed to start: %s", e)
            return None

    # ═══════════════════════════════════════════════════════════════
    #  任务分解
    # ═══════════════════════════════════════════════════════════════

    # [B6] 寒暄/闲聊关键词：简短寒暄不消耗 LLM 分解，直接走 rag 通用问答
    _GREETING_KEYWORDS = (
        "你好", "您好", "嗨", "哈喽", "在吗", "在不在", "hello", "hi", "hey",
        "早上好", "中午好", "下午好", "晚上好", "早安", "晚安", "早上", "晚上",
        "谢谢", "感谢", "辛苦了", "再见", "拜拜", "麻烦你", "请问", "你好呀",
        "hola", "yo", "hi there", "good morning", "good afternoon", "good evening",
    )

__all__ = ['SupervisorAgentBase']
