"""Agent 消息总线。

负责 Agent 的注册、消息路由、事件循环管理。
支持点对点发送、广播、以及 send_and_wait（发送并等待回复）模式。
"""

import asyncio
import logging
from collections import defaultdict
from typing import Optional

from app.agent.base import BaseAgent, AgentMessage

logger = logging.getLogger(__name__)


class AgentBus:
    """Agent 消息总线。

    用法:
        bus = AgentBus()
        bus.register(agent_a)
        bus.register(agent_b)
        bus.start_all()  # 启动所有 Agent 的事件循环
        await bus.send(AgentMessage(source="user", target="agent_a", ...))
    """

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._mailboxes: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._pending: dict[str, asyncio.Future] = {}  # thread_id → Future
        self._running: set[str] = set()
        self._tasks: list[asyncio.Task] = []  # 由 start_all() 创建的 task 列表

    # ------------------------------------------------------------------
    # 注册 / 发现
    # ------------------------------------------------------------------

    def register(self, agent: BaseAgent):
        """注册一个 Agent。"""
        aid = agent.agent_id
        if aid in self._agents:
            logger.warning("Agent '%s' already registered, overwriting", aid)
        self._agents[aid] = agent
        logger.info("✅ Agent registered: %s", aid)

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """按 ID 查找已注册的 Agent。"""
        return self._agents.get(agent_id)

    def list_agents(self) -> list[str]:
        """列出所有已注册的 Agent ID。"""
        return list(self._agents.keys())

    # ------------------------------------------------------------------
    # 消息发送
    # ------------------------------------------------------------------

    async def send(self, msg: AgentMessage):
        """发送一条消息。

        如果 msg.type == "response" 且有人正等待该 thread_id 的回复，
        则直接通过 Future 投递，不进入队列。
        否则将消息放入目标 Agent 的邮箱。
        """
        # ---- 如果有等待者，直接投递（不经过队列） ----
        if msg.type == "response" and msg.thread_id in self._pending:
            fut = self._pending.pop(msg.thread_id)
            if not fut.done():
                fut.set_result(msg)
            return

        # ---- 错误消息也支持等待者投递 ----
        if msg.type == "error" and msg.thread_id in self._pending:
            fut = self._pending.pop(msg.thread_id)
            if not fut.done():
                fut.set_exception(RuntimeError(msg.payload.get("error", "Unknown error")))
            return

        # ---- 正常路由 ----
        if msg.target == "*":
            for aid in self._agents:
                if aid != msg.source:
                    await self._mailboxes[aid].put(msg)
                    logger.debug("Broadcast %s → %s", msg.source, aid)
        elif msg.target in self._mailboxes:
            await self._mailboxes[msg.target].put(msg)
            logger.debug("Send %s → %s", msg.source, msg.target)
        else:
            logger.warning("Unknown target agent '%s', message dropped", msg.target)

    async def send_and_wait(
        self, msg: AgentMessage, timeout: float = 30.0
    ) -> AgentMessage:
        """发送消息并等待回复。

        Args:
            msg: 要发送的请求消息（type="request"）
            timeout: 超时秒数

        Returns:
            回复消息（type="response"）

        Raises:
            asyncio.TimeoutError: 如果在超时时间内未收到回复
        """
        assert msg.type == "request", "send_and_wait 只能用于 request 消息"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg.thread_id] = fut
        await self.send(msg)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg.thread_id, None)
            raise asyncio.TimeoutError(
                f"No reply from '{msg.target}' within {timeout}s "
                f"(thread={msg.thread_id}, action={msg.action})"
            )

    # ------------------------------------------------------------------
    # Agent 事件循环管理
    # ------------------------------------------------------------------

    async def run_agent(self, agent_id: str, max_retries: int = 5):
        """启动单个 Agent 的事件循环。

        从 mailbox 中取消息，调用 Agent 的 handle_message 处理，
        将产生的回复通过 bus.send 路由出去。
        在异常退出时自动重试（最多 max_retries 次）。
        """
        if agent_id in self._running:
            logger.debug("Agent '%s' already running", agent_id)
            return
        self._running.add(agent_id)

        agent = self._agents.get(agent_id)
        if not agent:
            logger.error("Agent '%s' not found, cannot start loop", agent_id)
            self._running.discard(agent_id)
            return

        queue = self._mailboxes[agent_id]
        logger.info("🔁 Agent event loop started: %s", agent_id)

        retry_count = 0
        while retry_count <= max_retries:
            try:
                while True:
                    msg = await queue.get()
                    try:
                        async for reply in agent.handle_message(msg):
                            await self.send(reply)
                    except Exception as e:
                        logger.error(
                            "Agent '%s' error handling %s/%s: %s",
                            agent_id, msg.action, msg.type, e, exc_info=True,
                        )
                        # 如果有等待者，要通知错误
                        if msg.thread_id in self._pending:
                            fut = self._pending.pop(msg.thread_id)
                            if not fut.done():
                                fut.set_exception(e)
            except asyncio.CancelledError:
                logger.info("⏹ Agent event loop cancelled: %s", agent_id)
                break
            except Exception as e:
                retry_count += 1
                if retry_count <= max_retries:
                    logger.warning(
                        "Agent '%s' crashed (attempt %d/%d), restarting in %ds: %s",
                        agent_id, retry_count, max_retries, retry_count, e,
                    )
                    await asyncio.sleep(retry_count)  # 递增延迟
                    queue = self._mailboxes[agent_id]
                else:
                    logger.error(
                        "Agent '%s' permanently stopped after %d retries: %s",
                        agent_id, retry_count, e, exc_info=True,
                    )
                    break

        self._running.discard(agent_id)

    def start_all(self):
        """启动所有已注册 Agent 的事件循环（非阻塞，返回 task 列表）。"""
        self._tasks = []
        for agent_id in self._agents:
            t = asyncio.create_task(self.run_agent(agent_id))
            self._tasks.append(t)
        logger.info("Started %d agent event loops", len(self._tasks))
        return self._tasks

    def stop_all(self):
        """取消所有 Agent 的事件循环。"""
        count = len(self._tasks)
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("Cancelled %d agent event loop(s)", count)
