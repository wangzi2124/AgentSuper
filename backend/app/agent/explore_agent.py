"""ExploreAgent — 只读代码探索 Agent（对齐 opencode explore subagent）。

只读工具（read/glob/grep/ls），快速探索代码库结构和内容。
不修改文件，专注于信息收集和分析。

支持的动作:
  - "chat":  使用只读工具探索代码库并回答问题
  - "explore": 直接返回文件结构/内容探索结果
"""

import logging
import time as tmod
from typing import AsyncIterator, Optional

from app.agent.base import BaseAgent, AgentMessage
from app.agent.agent_specs import get_agent_spec, get_agent_spec_or_none
from app.agent.memory import MemoryManager
from app.agent.stream_events import agent_meta, emit, step_event
from app.agent.sub_tools import tool_loop_chat
from app.config import settings

logger = logging.getLogger(__name__)

EXPLORE_SYSTEM_PROMPT = """你是一个快速代码探索助手。你的任务是帮助用户理解代码库的结构和内容。

核心原则:
- 只读操作：当前只暴露了只读工具（ls/read_file/glob/grep），写入/编辑/删除/执行等工具已被系统强制禁用，你无法修改任何文件
- 快速定位：使用 glob/grep 快速找到相关文件
- 深入分析：读取关键文件理解实现逻辑
- 结构化输出：清晰展示文件结构、代码逻辑、依赖关系

可用工具（只读，由系统强制）:
- tool_ls: 列出目录内容
- tool_read_file: 读取文件内容
- tool_glob: 按模式搜索文件名
- tool_grep: 按正则搜索文件内容

搜索深度约定（对齐 opencode explore 约定）:
- quick: 只做少量 glob/grep + 关键文件首屏读取
- medium: 额外检查相关实现与调用方
- very thorough: 覆盖多目录/多命名风格，读全关键文件后再总结

回答要求:
- 使用中文回答
- 引用具体文件路径和行号
- 展示关键代码片段
- 分析代码逻辑和设计模式"""


class ExploreAgent(BaseAgent):
    """只读代码探索 Agent。

    使用只读工具（read/glob/grep/ls）快速探索代码库，
    不修改任何文件，专注于信息收集和分析。
    """

    def __init__(
        self,
        memory: Optional[MemoryManager] = None,
        agent_id: str = "explore",
    ):
        self._id = agent_id
        self._memory = memory

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
                question = payload.get("question", "")
                conv_id = payload.get("conversation_id", "")
                event_queue = payload.get("_event_queue")
                history = payload.get("history") or []
                directory = payload.get("directory", "")
                name, avatar = agent_meta(self._id)
                emit(event_queue, {
                    "type": "agent_start",
                    "agent_id": self._id,
                    "agent_name": name,
                    "agent_avatar": avatar,
                })

                # [opencode 对齐] 工具 allowlist 由规格注册表（agent_specs.py）驱动：
                # explore 只暴露只读工具（ls/read/glob/grep），写/执行工具在 schema 层
                # 与运行时双重拒绝（permission '*': deny）。
                spec = get_agent_spec_or_none(self._id) or get_agent_spec("explore")

                # 使用只读工具循环探索
                start = tmod.time()
                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event("explore", "探索代码库", "running"),
                })

                answer = await tool_loop_chat(
                    system_prompt=EXPLORE_SYSTEM_PROMPT,
                    user_message=question,
                    event_queue=event_queue,
                    agent_id=self._id,
                    history=history,
                    directory=directory,
                    allowlist=spec.tools,
                )

                emit(event_queue, {
                    "type": "agent_step",
                    "agent_id": self._id,
                    "step": step_event(
                        "explore", "探索代码库", "completed",
                        duration_ms=(tmod.time() - start) * 1000,
                    ),
                })

                # 缓存到记忆
                if self._memory:
                    try:
                        await self._memory.set(
                            "explore_last_q",
                            question[:100],
                            ttl=120,
                            tags=["explore"],
                            namespace=conv_id,
                        )
                    except Exception:
                        pass

                emit(event_queue, {
                    "type": "agent_done",
                    "agent_id": self._id,
                    "content": answer,
                })
                yield AgentMessage(
                    source=self._id, target=msg.source,
                    type="response", action="chat",
                    payload={"answer": answer, "sources": [], "steps": []},
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
            logger.exception("ExploreAgent error on action=%s", action)
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
