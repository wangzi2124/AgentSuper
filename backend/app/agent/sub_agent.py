"""SubAgent — lightweight delegated agent with its own tool-call loop.

SubAgent runs as a subtask within the parent RAGAgent's tool-call loop.
It has its own model, system prompt, and tool set, and returns structured
results + metrics to the parent.
"""

import asyncio
import json
import logging
import time as tmod
from typing import Optional

import litellm

from app.agent.tools import ToolDef
from app.context.token_counter import truncate_messages as _truncate_messages
from app.context.tool_output import bound_tool_output
from app.monitor import record_model_call

logger = logging.getLogger(__name__)


class SubAgent:
    """Lightweight sub-agent with its own model, tools, and tool-call loop.

    The parent agent delegates a task via a tool call; this class runs the
    delegated loop independently and returns the final answer.
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        tools: list[ToolDef],
        model: str,
        api_key: str,
        api_base: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        max_tool_rounds: int = 20,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tool_rounds = max_tool_rounds

    # -- LLM call (same pattern as RAGAgent._llm_call) -------------------

    async def _llm_call(
        self, messages: list, tool_defs: list | None
    ) -> litellm.ModelResponse:
        kwargs: dict = dict(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            api_base=self.api_base,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=300,
            num_retries=1,
        )
        if tool_defs:
            kwargs["tools"] = tool_defs
        return await litellm.acompletion(**kwargs)

    # -- Main entry point ------------------------------------------------

    async def run(
        self,
        task: str,
        context: str = "",
        event_queue=None,
    ) -> dict:
        """Execute the delegated task with an independent tool-call loop.

        Returns:
            dict with keys: result, tool_rounds, prompt_tokens,
                            completion_tokens, duration_ms
        """
        start = tmod.time()

        full_system = self.system_prompt
        if context:
            full_system += f"\n\nAdditional Context:\n{context}"

        tool_defs = None
        if self.tools:
            tool_defs = [t.to_openai_tool() for t in self.tools]

        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": task},
        ]

        if event_queue:
            await event_queue.put({
                "type": "subagent_start",
                "step_id": f"delegate_{self.name}",
                "name": self.name,
                "model": self.model,
            })

        response = await self._llm_call(messages, tool_defs)
        msg = response.choices[0].message

        total_pt = (
            getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
        )
        total_ct = (
            getattr(response.usage, "completion_tokens", 0) if response.usage else 0
        )
        rounds = 0

        # Tool-call loop
        while msg.tool_calls and rounds < self.max_tool_rounds:
            rounds += 1

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            async def _exec_single(tc):
                tool_name = tc.function.name
                try:
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                except json.JSONDecodeError as e:
                    return tc.id, f"Error parsing arguments for '{tool_name}': {e}"
                for t in self.tools:
                    if t.name == tool_name:
                        try:
                            result = await asyncio.to_thread(t.fn, **args)
                            return tc.id, str(result)
                        except Exception as e:
                            return tc.id, f"Error executing {tool_name}: {e}"
                return tc.id, f"Tool '{tool_name}' not found"

            results = await asyncio.gather(
                *[_exec_single(tc) for tc in msg.tool_calls]
            )

            for tc_id, result in results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": bound_tool_output(result, "sub_agent_tool"),
                })

            messages = _truncate_messages(messages)
            response = await self._llm_call(messages, tool_defs)
            msg = response.choices[0].message
            usage = getattr(response, "usage", None)
            if usage:
                total_pt += getattr(usage, "prompt_tokens", 0) or 0
                total_ct += getattr(usage, "completion_tokens", 0) or 0

        answer = (msg.content or "").strip() or "任务完成"
        duration = (tmod.time() - start) * 1000

        record_model_call(
            self.model,
            prompt_tokens=total_pt,
            completion_tokens=total_ct,
            duration_ms=duration,
            tool_rounds=rounds,
        )

        if event_queue:
            await event_queue.put({
                "type": "subagent_end",
                "step_id": f"delegate_{self.name}",
                "name": self.name,
                "result": answer[:500],
                "tool_rounds": rounds,
                "prompt_tokens": total_pt,
                "completion_tokens": total_ct,
                "duration_ms": round(duration, 1),
            })

        return {
            "result": answer,
            "tool_rounds": rounds,
            "prompt_tokens": total_pt,
            "completion_tokens": total_ct,
            "duration_ms": round(duration, 1),
        }
