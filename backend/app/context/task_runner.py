"""任务执行引擎 — 持久化 Agent 执行引擎。

流程：
1. RAG 检索 + 重排序
2. CrewAI 多 Agent 团队生成回答（端到端处理）
3. 任务状态持久化到 SQLite
"""

import asyncio
import logging

from app.context.task_state import TaskState

logger = logging.getLogger(__name__)


class TaskRunner:
    """Persistent task execution engine (CrewAI-native).

    Flow:
    1. Run RAG pipeline via agent.invoke() (retrieve -> rerank -> CrewAI generate)
    2. Return result (CrewAI handles all tool calls internally)
    """

    def __init__(self, agent: "RAGAgent"):
        self.agent = agent

    async def run(
        self,
        question: str,
        model: str | None = None,
        history: list[dict] | None = None,
        use_vector_db: bool = True,
        files: list[dict] | None = None,
        event_queue: asyncio.Queue | None = None,
        conversation_id: str = "",
    ) -> dict:
        """Execute a full task: RAG retrieval -> CrewAI multi-agent generation."""
        task = TaskState(conversation_id=conversation_id)
        task.save()
        logger.info("Task %s started for conversation %s", task.task_id, conversation_id)

        try:
            result = await self.agent.invoke(
                question=question,
                model=model,
                history=history or [],
                use_vector_db=use_vector_db,
                files=files or [],
                event_queue=event_queue,
            )
            task.mark_completed()
            logger.info(
                "Task %s completed: %d steps",
                task.task_id, task.step,
            )
            result["task"] = task.to_dict()
            return result
        except Exception as e:
            task.mark_failed(str(e))
            raise
