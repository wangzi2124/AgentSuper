"""Task runner — agent execution wrapper.

Runs the LangGraph RAG pipeline (retrieve → rerank → generate) with
task-state tracking and persistence. The tool-call loop is handled
entirely inside graph.py:_generate, so this wrapper focuses on:
- TaskState creation and lifecycle
- Event queue plumbing for SSE streaming
- Execution logging and statistics
"""

import asyncio
import json
import logging
import time as tmod
from typing import Any, Optional

from app.context.compaction import ContextCompactor
from app.context.task_state import TaskState
from app.config import settings

logger = logging.getLogger(__name__)

# Compaction threshold
COMPACTION_THRESHOLD = 80_000


class TaskRunner:
    """Persistent task execution engine.

    Wraps the agent's LLM call + tool execution into a loop that continues
    until the task is naturally complete (LLM stops calling tools) or
    the max step limit is reached.
    """

    def __init__(self, agent: "RAGAgent"):
        self.agent = agent
        self.compactor = ContextCompactor(
            model=settings.summarization_model or settings.llm_model,
            api_key=settings.summarization_api_key or settings.llm_api_key,
            api_base=settings.summarization_api_base or settings.llm_api_base,
            threshold=COMPACTION_THRESHOLD,
        )

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
        """Execute a full task with persistent agent loop.

        This is the main entry point. It:
        1. Runs the initial RAG pipeline (retrieve → rerank → generate)
        2. If the LLM produced tool calls, continues the loop
        3. Handles compaction when context grows large
        4. Persists task state throughout

        Returns the same dict as agent.invoke(): {answer, sources, steps, messages}
        """
        task = TaskState(conversation_id=conversation_id)
        task.save()

        logger.info("Task %s started for conversation %s", task.task_id, conversation_id)

        try:
            result = await self._run_loop(
                question=question,
                model=model,
                history=history or [],
                use_vector_db=use_vector_db,
                files=files or [],
                event_queue=event_queue,
                task=task,
            )
            task.mark_completed()
            logger.info(
                "Task %s completed: %d steps, %d tokens, %d tool calls",
                task.task_id, task.step, task.total_tokens, task.tool_calls_count,
            )
            result["task"] = task.to_dict()
            return result

        except Exception as e:
            task.mark_failed(str(e))
            raise

    async def _run_loop(
        self,
        question: str,
        model: str | None,
        history: list[dict],
        use_vector_db: bool,
        files: list[dict],
        event_queue: asyncio.Queue | None,
        task: TaskState,
    ) -> dict:
        """Core execution loop.

        The LangGraph pipeline (retrieve → rerank → generate) already handles
        the full tool-call loop internally (up to 50 rounds in _generate).
        This method runs it once and returns the result — no continuation loop
        needed, eliminating the redundant dual-agent-loop architecture.
        """
        agent_state = await self._run_initial_rag(
            question, model, history, use_vector_db, files, event_queue, task
        )

        return {
            "answer": agent_state.get("answer", ""),
            "sources": agent_state.get("sources", []),
            "steps": agent_state.get("steps", []),
        }

    async def _run_initial_rag(
        self,
        question: str,
        model: str | None,
        history: list[dict],
        use_vector_db: bool,
        files: list[dict],
        event_queue: asyncio.Queue | None,
        task: TaskState,
    ) -> dict:
        """Run the initial RAG pipeline via LangGraph and return the full state."""
        from langchain_core.messages import HumanMessage

        state = {
            "messages": [HumanMessage(content=question)],
            "question": question,
            "context": [],
            "answer": "",
            "sources": [],
            "model": model,
            "history": history,
            "use_vector_db": use_vector_db,
            "files": files,
            "steps": [],
            "_event_queue": event_queue,
        }

        # Run the graph
        result = await self.agent.graph.ainvoke(state)

        # Extract the LLM messages from the graph execution
        # The _generate node builds messages internally; we need to reconstruct
        # by running _generate's message-building logic again with state capture
        llm_messages = await self._capture_llm_messages(result, question, model, history, files)

        return {
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "steps": result.get("steps", []),
            "_llm_messages": llm_messages,
        }

    async def _capture_llm_messages(
        self,
        graph_result: dict,
        question: str,
        model: str | None,
        history: list[dict],
        files: list[dict],
    ) -> list[dict]:
        """Reconstruct the LLM message list from graph execution.

        Since LangGraph doesn't expose the internal messages array,
        we rebuild it from the known state.
        """
        # Build system prompt
        if graph_result.get("context"):
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(graph_result["context"])
            ]
            context_text = "\n\n".join(context_parts)
            system_prompt = (
                self.agent._system_prompt_with_kb()
                + "\n\n"
                + f"Retrieved Context:\n{context_text}"
            )
        else:
            system_prompt = self.agent.system_prompt

        messages = [{"role": "system", "content": system_prompt}]

        if history:
            messages.extend(history)

        # User message
        if files:
            user_content: list[dict] = [{"type": "text", "text": question}]
            for f in files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": question})

        # Add the assistant's answer
        answer = graph_result.get("answer", "")
        if answer:
            messages.append({"role": "assistant", "content": answer})

        return messages

    def _push_event(self, event_queue: asyncio.Queue | None, event: dict):
        """Push an event to the queue if available."""
        if event_queue:
            try:
                event_queue.put_nowait(event)
            except Exception:
                pass
