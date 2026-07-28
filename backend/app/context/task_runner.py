"""任务执行引擎 — 持久化 Agent 执行引擎。

参考 OpenCode 的双层 while 循环架构：
- 内层循环：LLM + 工具调用持续到 LLM 不再返回 tool_calls
- 外层循环：检查是否有用户追加输入
- 上下文压缩：消息超 80K tokens 时自动压缩旧消息
- 最大步数：超限后注入强制总结 prompt

核心功能：
- 任务状态持久化到 SQLite，支持崩溃恢复
- 工具结果去重，避免重复执行相同调用
- 智能输出边界控制，防止大输出撑爆上下文
- 指数退避重试机制

Rewritten to remove LangGraph dependency — now uses direct LLM calls
and the RAGAgent's tool execution for the continuation loop.
"""

import asyncio
import json
import logging
from typing import Any

from app.context.compaction import ContextCompactor
from app.context.task_state import TaskState
from app.context.token_counter import truncate_messages
from app.context.tool_output import bound_tool_output
from app.context.tool_dedup import ToolResultDedup
from app.config import settings

logger = logging.getLogger(__name__)

MAX_STEPS = 50
COMPACTION_THRESHOLD = 80_000

RETRY_MAX_ATTEMPTS = 3
RETRY_INITIAL_DELAY = 2.0
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY = 30.0


def _is_retryable_error(exc: Exception) -> bool:
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__
    if "ratelimit" in exc_type or "rate_limit" in exc_str or "429" in exc_str:
        return True
    if "too many requests" in exc_str or "rate limit" in exc_str:
        return True
    if "internalserversrror" in exc_type or "500" in exc_str or "502" in exc_str or "503" in exc_str:
        return True
    if "server" in exc_str and ("error" in exc_str or "unavailable" in exc_str):
        return True
    if "overloaded" in exc_str or "service_unavailable" in exc_str or "exhausted" in exc_str:
        return True
    if "timeout" in exc_str or "timed out" in exc_str:
        return True
    if "connection" in exc_str and ("error" in exc_str or "refused" in exc_str or "reset" in exc_str):
        return True
    if "network" in exc_str:
        return True
    if "litellm" in exc_type.lower():
        if any(code in exc_str for code in ("429", "500", "502", "503", "504")):
            return True
    return False


def _compute_retry_delay(attempt: int, exc: Exception) -> float:
    exc_str = str(exc).lower()
    import re
    retry_after_match = re.search(r'retry[-_]?after[:\s]*(\d+)', exc_str)
    if retry_after_match:
        delay = float(retry_after_match.group(1))
        return min(delay, RETRY_MAX_DELAY)
    delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
    return min(delay, RETRY_MAX_DELAY)


MAX_STEPS_PROMPT = (
    "You have reached the maximum number of steps for this task. "
    "Please provide a comprehensive summary of:\n"
    "1. What you have completed so far\n"
    "2. What still needs to be done\n"
    "3. Any important files, decisions, or context\n"
    "Do NOT make any more tool calls. Respond only with text."
)


class TaskRunner:
    """Persistent task execution engine (CrewAI version).

    Flow:
    1. Run RAG pipeline via agent.invoke() (retrieve → rerank → generate with CrewAI)
    2. If LLM produced tool calls in the continuation loop, keep going
    3. Handle compaction and max steps
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
        """Execute a full task with persistent agent loop."""
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

        Phase 1: Run full RAG pipeline via agent.invoke()
        Phase 2: Continue if LLM produced tool calls (continuation loop)
        """
        dedup = ToolResultDedup()
        all_steps: list[dict] = []
        all_sources: list[dict] = []

        # --- Phase 1: RAG pipeline (retrieve → rerank → CrewAI generate) ---
        rag_result = await self.agent.invoke(
            question=question,
            model=model,
            history=history,
            use_vector_db=use_vector_db,
            files=files,
            event_queue=event_queue,
        )

        answer = rag_result.get("answer", "")
        all_sources = rag_result.get("sources", [])
        all_steps = rag_result.get("steps", [])

        # Rebuild LLM messages for continuation loop
        messages = self._build_messages(question, model, history, files, answer, rag_result)

        if not messages:
            return {"answer": answer, "sources": all_sources, "steps": all_steps}

        # --- Phase 2: Continuation loop (if LLM wants more tool calls) ---
        # Check if there are pending tool calls in the last assistant message
        last_msg = messages[-1] if messages else None
        needs_continuation = (
            last_msg
            and last_msg.get("role") == "assistant"
            and last_msg.get("tool_calls")
        )
        if not answer.strip() and not needs_continuation:
            needs_continuation = True

        turn = 0
        while needs_continuation and task.step < MAX_STEPS:
            turn += 1
            task.increment_step()

            # Compaction check
            if self.compactor.should_compact(messages):
                event = {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"}
                self._push_event(event_queue, event)
                old_count = len(messages)
                messages = await self.compactor.compact(messages)
                task.record_compaction()
                event = {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"}
                self._push_event(event_queue, event)

            tool_defs = self.agent._build_tool_defs()
            is_last_step = task.step >= MAX_STEPS - 1
            if is_last_step:
                logger.warning("Task %s: max steps (%d) reached, forcing summary", task.task_id, MAX_STEPS)
                messages.append({"role": "user", "content": MAX_STEPS_PROMPT})

            resolved_model = model or self.agent.model
            if "/" not in resolved_model:
                if self.agent.api_base and "deepseek" in self.agent.api_base:
                    resolved_model = f"deepseek/{resolved_model}"
                elif self.agent.api_base and "openai" in self.agent.api_base:
                    resolved_model = f"openai/{resolved_model}"

            messages = truncate_messages(messages)

            # LLM call with retry
            response = None
            last_exc = None
            for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
                try:
                    response = await self.agent._llm_call(
                        resolved_model, messages,
                        tool_defs if not is_last_step else None,
                    )
                    break
                except Exception as e:
                    last_exc = e
                    if not _is_retryable_error(e) or attempt >= RETRY_MAX_ATTEMPTS:
                        logger.error("Task %s: LLM call failed at step %d (attempt %d/%d): %s",
                                     task.task_id, task.step, attempt, RETRY_MAX_ATTEMPTS, e)
                        break
                    delay = _compute_retry_delay(attempt, e)
                    logger.warning("Task %s: retrying step %d (attempt %d/%d) in %.1fs: %s",
                                   task.task_id, task.step, attempt, RETRY_MAX_ATTEMPTS, delay, e)
                    self._push_event(event_queue, {
                        "type": "step_start", "step_id": "retry", "name": "重试中",
                        "status": "running", "detail": f"第 {attempt} 次重试，{delay:.0f}s 后重试",
                    })
                    await asyncio.sleep(delay)
                    self._push_event(event_queue, {
                        "type": "step_end", "step_id": "retry", "name": "重试中",
                        "status": "completed", "detail": f"正在重试（第 {attempt + 1} 次）",
                    })

            if response is None:
                if last_exc:
                    raise last_exc
                break

            msg = response.choices[0].message

            usage = getattr(response, "usage", None)
            if usage:
                pt = getattr(usage, "prompt_tokens", 0) or 0
                ct = getattr(usage, "completion_tokens", 0) or 0
                task.add_tokens(pt + ct)

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

            messages = truncate_messages(messages)

        if needs_continuation and task.step >= MAX_STEPS:
            while messages and messages[-1].get("role") in ("tool",):
                messages.pop()

        return {
            "answer": answer,
            "sources": all_sources,
            "steps": all_steps,
        }

    def _build_messages(
        self,
        question: str,
        model: str | None,
        history: list[dict],
        files: list[dict],
        answer: str,
        rag_result: dict,
    ) -> list[dict]:
        """Build the LLM message list from the RAG result for continuation."""
        # System prompt
        context = rag_result.get("context", []) if hasattr(rag_result, 'get') else []
        if context:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(context)
            ]
            context_text = "\n\n".join(context_parts)
            system_prompt = (
                self.agent._system_prompt_with_kb()
                + "\n\nRetrieved Context:\n" + context_text
            )
        else:
            system_prompt = self.agent.system_prompt

        messages = [{"role": "system", "content": system_prompt}]

        if history:
            messages.extend(history)

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

        if answer:
            messages.append({"role": "assistant", "content": answer})

        return messages

    def _push_event(self, event_queue: asyncio.Queue | None, event: dict):
        if event_queue:
            try:
                event_queue.put_nowait(event)
            except Exception:
                pass
