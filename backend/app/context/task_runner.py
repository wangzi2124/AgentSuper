"""Task runner — persistent agent execution engine.

Inspired by OpenCode's double-while-loop architecture:
- Inner loop: LLM + tool calls until LLM stops requesting tools
- Outer loop: check for user steer input or continuation
- Compaction: compress old messages when context grows too large
- Max steps: force summary when step limit is reached

This module orchestrates multi-turn agent execution with state persistence,
ensuring tasks run to completion without premature termination.
"""

import asyncio
import json
import logging
import time as tmod
from typing import Any, Optional

from app.context.compaction import ContextCompactor
from app.context.task_state import TaskState
from app.context.token_counter import estimate_tokens, truncate_messages
from app.context.tool_output import bound_tool_output
from app.context.tool_dedup import ToolResultDedup
from app.config import settings

logger = logging.getLogger(__name__)

# Max steps before forced summary
MAX_STEPS = 50

# Compaction threshold
COMPACTION_THRESHOLD = 80_000

# Forced summary prompt (injected when max_steps is reached)
MAX_STEPS_PROMPT = (
    "You have reached the maximum number of steps for this task. "
    "Please provide a comprehensive summary of:\n"
    "1. What you have completed so far\n"
    "2. What still needs to be done\n"
    "3. Any important files, decisions, or context\n"
    "Do NOT make any more tool calls. Respond only with text."
)


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
        """Core execution loop — the double-while pattern.

        Inner: LLM call + tool execution until LLM stops calling tools
        Outer: check for continuation (currently single-pass, extendable)
        """
        dedup = ToolResultDedup()
        all_steps: list[dict] = []
        all_sources: list[dict] = []

        # --- Phase 1: Initial RAG pipeline (retrieve → rerank → generate) ---
        # Run the LangGraph workflow for the first turn
        agent_state = await self._run_initial_rag(
            question, model, history, use_vector_db, files, event_queue, task
        )

        # Extract results from the initial turn
        messages = agent_state.get("_llm_messages", [])
        answer = agent_state.get("answer", "")
        all_sources = agent_state.get("sources", [])
        all_steps = agent_state.get("steps", [])

        # If the initial turn didn't produce messages, we're done
        if not messages:
            return {"answer": answer, "sources": all_sources, "steps": all_steps}

        # --- Phase 2: Continue if LLM wants more tool calls ---
        # Check if the last assistant message has unfinished tool calls
        last_msg = messages[-1] if messages else None
        needs_continuation = (
            last_msg
            and last_msg.get("role") == "assistant"
            and last_msg.get("tool_calls")
        )

        # Also continue if answer is empty (LLM returned nothing useful)
        if not answer.strip() and not needs_continuation:
            needs_continuation = True

        turn = 0
        while needs_continuation and task.step < MAX_STEPS:
            turn += 1
            task.increment_step()

            # Check for compaction
            if self.compactor.should_compact(messages):
                event = {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"}
                self._push_event(event_queue, event)
                old_count = len(messages)
                messages = await self.compactor.compact(messages)
                task.record_compaction()
                event = {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"}
                self._push_event(event_queue, event)

            # Build tool definitions
            tool_defs = self.agent._build_tool_defs()

            # Check if this is the last step — force text-only response
            is_last_step = task.step >= MAX_STEPS - 1
            if is_last_step:
                logger.warning("Task %s: max steps (%d) reached, forcing summary", task.task_id, MAX_STEPS)
                messages.append({"role": "user", "content": MAX_STEPS_PROMPT})

            # Resolve model name
            resolved_model = model or self.agent.model
            if "/" not in resolved_model:
                if self.agent.api_base and "deepseek" in self.agent.api_base:
                    resolved_model = f"deepseek/{resolved_model}"
                elif self.agent.api_base and "openai" in self.agent.api_base:
                    resolved_model = f"openai/{resolved_model}"

            # Truncate messages before LLM call
            messages = truncate_messages(messages)

            # LLM call
            try:
                response = await self.agent._llm_call(resolved_model, messages, tool_defs if not is_last_step else None)
            except Exception as e:
                logger.error("Task %s: LLM call failed at step %d: %s", task.task_id, task.step, e)
                break

            msg = response.choices[0].message

            # Record token usage
            usage = getattr(response, "usage", None)
            if usage:
                pt = getattr(usage, "prompt_tokens", 0) or 0
                ct = getattr(usage, "completion_tokens", 0) or 0
                task.add_tokens(pt + ct)

            # If no tool calls, we're done
            if not msg.tool_calls:
                answer = msg.content or answer
                messages.append({"role": "assistant", "content": answer})
                needs_continuation = False
                break

            # Execute tool calls
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            tool_tasks = []
            tool_metas = []
            early_results: dict[str, str] = {}

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError as e:
                    early_results[tc.id] = f"Error parsing arguments for '{tool_name}': {e}"
                    continue

                dedup_key = dedup.make_key(tool_name, args)
                cached = dedup.get(dedup_key)
                if cached is not None:
                    early_results[tc.id] = cached
                    self._push_event(event_queue, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                    continue

                self._push_event(event_queue, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                tool_tasks.append(self.agent._execute_tool(tool_name, args))
                tool_metas.append((tc.id, tool_name, dedup_key))

            if tool_tasks:
                tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
            else:
                tool_results = []

            for (tc_id, tool_name, dkey), result in zip(tool_metas, tool_results):
                if isinstance(result, Exception):
                    result = f"Error executing {tool_name}: {result}"
                result_str = str(result)
                dedup.set(dkey, result_str)
                early_results[tc_id] = result_str
                task.tool_calls_count += 1

            for tc in msg.tool_calls:
                tc_id = tc.id
                result_str = early_results.get(tc_id, f"Error: no result for tool call {tc_id}")
                bounded_result = bound_tool_output(result_str, tc.function.name)
                tool_name = tc.function.name
                self._push_event(event_queue, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": bounded_result,
                })

            # Truncate after tool results
            messages = truncate_messages(messages)

        # If we exited the loop with tool_calls still pending, force a final answer
        if needs_continuation and task.step >= MAX_STEPS:
            # Remove any trailing tool_calls from messages (incompatible with final answer)
            while messages and messages[-1].get("role") in ("tool",):
                messages.pop()

        return {
            "answer": answer,
            "sources": all_sources,
            "steps": all_steps,
            "_llm_messages": messages,
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
        from app.context.token_counter import estimate_tokens

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
