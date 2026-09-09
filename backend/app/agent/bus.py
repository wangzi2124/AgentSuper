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
        # [opencode abort 级联] thread_id → 正在处理该消息的 handler task。
        # run_agent 每处理一条消息都会登记；abort(thread_id) 取消它即可中断在途子任务。
        self._active_handlers: dict[str, asyncio.Task] = {}

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
        # [A3] 下次使用前先清理已完成的遗留 Future（防止异常路径残留导致内存泄漏）
        self.prune_done_pending()
        fut = loop.create_future()
        self._pending[msg.thread_id] = fut
        # [A3] Future 完成后兜底移除 _pending 项：无论走 send() 直投 / 超时 / 取消 / GC，
        # 都保证 thread_id → Future 不会残留在 _pending 中（若 send() 已弹出则为空操作）。
        # [opencode abort 级联] 等待方自身被取消（父任务 abort）时，级联取消被等待的
        # 子线程 handler task —— 等待方取消不止"不再等"，还真正中断在途子任务。
        fut.add_done_callback(lambda _f, tid=msg.thread_id: self._pending.pop(tid, None))
        fut.add_done_callback(
            lambda _f, tid=msg.thread_id: (self.abort_work(tid) if _f.cancelled() else None)
        )
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
        except asyncio.CancelledError:
            # [opencode abort 级联] 等待方自身被取消（父任务 abort/中断）→
            # 级联取消被等待线程的实际执行 handler，使子任务真正停跑。
            self._pending.pop(msg.thread_id, None)
            self.abort_work(msg.thread_id)
            raise
        except BaseException:
            self._pending.pop(msg.thread_id, None)
            raise

    def prune_done_pending(self) -> int:
        """[A3] 移除 _pending 中已完成/已取消的 Future（防御性清理，防止内存泄漏）。

        正常路径由 send() 直投 / 超时 / done-callback 清理，此方法兜底扫描。
        Returns:
            清理掉的条目数。
        """
        removed = 0
        for tid in list(self._pending.keys()):
            fut = self._pending[tid]
            if fut.done():
                self._pending.pop(tid, None)
                removed += 1
        return removed

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

    def abort_work(self, thread_id: str) -> bool:
        """取消指定 thread 上正在实际执行的 handler task（级联取消的工作侧）。

        对齐 opencode `ops.cancel`：取消等待（cancel_pending）只停"等"，真正中断
        在途子任务需要取消 run_agent 正在跑该消息的 handler task。
        """
        handler = self._active_handlers.get(thread_id)
        if handler is not None and not handler.done():
            logger.warning("Aborting in-flight handler (thread=%s)", thread_id)
            handler.cancel()
            return True
        return False

    def abort(self, thread_id: str) -> int:
        """级联取消一个 thread：先停等待方，再中断执行方。

        Returns:
            实际取消的数量（等待 future + handler task，0-2）。
        """
        cancelled = 0
        if self.cancel_pending(thread_id):
            cancelled += 1
        if self.abort_work(thread_id):
            cancelled += 1
        return cancelled

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
                    # [opencode abort 级联] 每条消息的实现在独立 handler task 中运行，
                    # 供 abort_work(thread_id) 精确中断在途子任务。
                    handler = asyncio.create_task(self._dispatch(msg, agent_id))
                    self._active_handlers[msg.thread_id] = handler
                    try:
                        await handler
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        # 分支：handler 被 abort_work 单独取消（当前任务未被取消），
                        # 吞掉 CancelledError 继续处理下一条消息。
                        if task is not None and task.cancelling() == 0:
                            logger.info("Aborted handler for thread=%s on %s", msg.thread_id, agent_id)
                        else:
                            raise
                    finally:
                        self._active_handlers.pop(msg.thread_id, None)
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

    async def _dispatch(self, msg: AgentMessage, agent_id: str) -> None:
        """把一条消息交给 agent 处理并路由其回复；错误以结构化消息交付。

        独立协程（由 run_agent create_task），便于 abort_work 单独取消。
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return
        try:
            async for reply in agent.handle_message(msg):
                self.touch(agent_id)
                await self.send(reply)
        except asyncio.CancelledError:
            raise
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
