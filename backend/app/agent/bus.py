"""Agent 消息总线。

负责 Agent 的注册、消息路由、事件循环管理。
支持点对点发送、广播、以及 send_and_wait（发送并等待回复）模式。
"""

import asyncio
import logging
import time as tmod
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
        self._mailboxes: dict[str, asyncio.Queue] = {}
        self._pending: dict[str, asyncio.Future] = {}  # thread_id → Future
        self._running: set[str] = set()
        self._tasks: list[asyncio.Task] = []  # 由 start_all() 创建的 task 列表
        # 心跳：agent 事件循环处理消息时更新的活动时间（秒时间戳），
        # 供 send_and_wait 判断"子 Agent 是否仍在处理"以决定是否延长等待。
        self._agent_activity: dict[str, float] = {}
        # 处理进度：子 Agent 最近完成/进行中的步骤描述（最多保留 8 条），
        # 供 supervisor 在子 Agent 超时时回传"已完成步骤"上下文。
        self._agent_progress: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # 注册 / 发现
    # ------------------------------------------------------------------

    def register(self, agent: BaseAgent):
        """注册一个 Agent。"""
        aid = agent.agent_id
        if aid in self._agents:
            logger.warning("Agent '%s' already registered, overwriting", aid)
        self._agents[aid] = agent
        self._mailboxes[aid] = asyncio.Queue()
        logger.info("✅ Agent registered: %s", aid)

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """按 ID 查找已注册的 Agent。"""
        return self._agents.get(agent_id)

    def list_agents(self) -> list[str]:
        """列出所有已注册的 Agent ID。"""
        return list(self._agents.keys())

    # ------------------------------------------------------------------
    # 心跳 / 进度
    # ------------------------------------------------------------------

    def touch(self, agent_id: str, progress: str = "") -> None:
        """更新子 Agent 的活动心跳与处理进度。

        在子 Agent 处理期间（含长时间工具循环）持续调用，供
        send_and_wait 的宽限续期判断"仍在运行"；progress 可附带最近
        完成/进行中的步骤描述，超时时作为"已完成步骤"回传。
        """
        self._agent_activity[agent_id] = tmod.time()
        if progress:
            steps = self._agent_progress.setdefault(agent_id, [])
            if steps and steps[-1] == progress:
                return
            steps.append(progress)
            if len(steps) > 8:
                self._agent_progress[agent_id] = steps[-8:]

    def agent_progress(self, agent_id: str) -> list[str]:
        """返回子 Agent 最近的处理进度（已完成步骤的描述列表）。"""
        return list(self._agent_progress.get(agent_id, []))

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
        # 以 AgentMessage(type="error") 交付而非裸异常，调用方（supervisor）可读取
        # payload 中的 completed_steps 等上下文，无需再捕获 RuntimeError。
        if msg.type == "error" and msg.thread_id in self._pending:
            fut = self._pending.pop(msg.thread_id)
            if not fut.done():
                fut.set_result(msg)
            return

        # ---- 正常路由 ----
        if msg.target == "*":
            for aid in self._agents:
                if aid != msg.source:
                    await self._mailboxes[aid].put(msg)
                    logger.debug("Broadcast %s → %s", msg.source, aid)
        elif msg.target in self._agents:
            await self._mailboxes[msg.target].put(msg)
            logger.debug("Send %s → %s", msg.source, msg.target)
        else:
            logger.warning("Unknown target agent '%s', message dropped", msg.target)

    async def send_and_wait(
        self, msg: AgentMessage, timeout: float = 30.0,
        grace_extensions: int = 1, grace_window: Optional[float] = None,
    ) -> AgentMessage:
        """发送消息并等待回复（支持分级超时）。

        Args:
            msg: 要发送的请求消息（type="request"）
            timeout: 基础超时秒数
            grace_extensions: 子 Agent 仍在活动时最多额外延长的次数（默认 1）
            grace_window: 判定"仍在活动"的时间窗口（秒）；默认 max(10, timeout/2)

        Returns:
            回复消息（type="response"）

        Raises:
            asyncio.TimeoutError: 超时（含宽限延长）仍无回复
        """
        assert msg.type == "request", "send_and_wait 只能用于 request 消息"
        if grace_window is None:
            grace_window = max(10.0, timeout / 2)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg.thread_id] = fut
        await self.send(msg)
        try:
            deadline = loop.time() + timeout
            extensions = max(0, int(grace_extensions))
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    # 分级超时：目标 Agent 仍在处理消息（事件循环活跃）时延长一次等待，
                    # 避免工具密集型子任务（脚手架/构建）在 LLM+工具循环中被过早判定超时。
                    last_active = self._agent_activity.get(msg.target, 0.0)
                    if extensions > 0 and last_active >= loop.time() - grace_window:
                        extensions -= 1
                        deadline = loop.time() + timeout
                        logger.warning(
                            "Sub-agent '%s' still active, extending wait by %.0fs (thread=%s)",
                            msg.target, timeout, msg.thread_id,
                        )
                        remaining = deadline - loop.time()
                    else:
                        raise asyncio.TimeoutError(
                            f"No reply from '{msg.target}' within {timeout}s "
                            f"(thread={msg.thread_id}, action={msg.action})"
                        )
                done, _ = await asyncio.wait({fut}, timeout=remaining)
                if fut in done:
                    return fut.result()
        except asyncio.TimeoutError:
            self._pending.pop(msg.thread_id, None)
            raise
        except BaseException:
            self._pending.pop(msg.thread_id, None)
            raise

    def cancel_pending(self, thread_id: str) -> bool:
        """取消指定 thread 的等待（级联取消用，对齐 opencode abort）。

        Returns:
            True 表示确实取消了一个尚未完成的 future。
        """
        fut = self._pending.pop(thread_id, None)
        if fut is not None and not fut.done():
            fut.cancel()
            return True
        return False

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
                    self.touch(agent_id)
                    try:
                        async for reply in agent.handle_message(msg):
                            self.touch(agent_id)
                            await self.send(reply)
                    except Exception as e:
                        logger.error(
                            "Agent '%s' error handling %s/%s: %s",
                            agent_id, msg.action, msg.type, e, exc_info=True,
                        )
                        # 如果有等待者，投递结构化错误消息而非裸异常，
                        # 让调用方拿到 error payload（含已完成步骤等上下文）。
                        if msg.thread_id in self._pending:
                            fut = self._pending.pop(msg.thread_id)
                            if not fut.done():
                                fut.set_result(AgentMessage(
                                    source=agent_id,
                                    target=msg.source,
                                    type="error",
                                    action=msg.action,
                                    payload={
                                        "error": str(e),
                                        "error_type": "sub_agent_error",
                                        "completed_steps": self.agent_progress(agent_id),
                                    },
                                    thread_id=msg.thread_id,
                                ))
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
