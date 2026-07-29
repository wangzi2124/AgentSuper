"""RAG Agent — direct litellm tool-call loop (replaces LangGraph).

Architecture:
  1. Pre-processing: retrieve → rerank (same as before)
  2. Generation: litellm direct tool-call loop with compaction/dedup/retry
  3. CrewAI is used only for standalone multi-agent tasks (crew_manager.py)
"""

import asyncio
import json
import logging
import subprocess
import threading
import time as tmod
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parents[2]

from app.context.token_counter import truncate_messages as _truncate_messages
from app.context.tool_output import bound_tool_output
from app.context.tool_dedup import ToolResultDedup

MAX_CONTEXT_TOKENS = 1_000_000

import litellm
from app.rag.retriever import Retriever
from app.rag.reranker import Reranker
from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader
from app.config import settings
from app.agent.sub_agent import SubAgent
from app.agent.tools import (
    ToolDef,
    create_filesystem_tools,
    create_skill_tools,
    create_plugin_tools,
    create_delegate_tool,
    create_delegate_crew_tool,
    build_system_prompt_no_kb,
)
from app.monitor import record_model_call
from app.permission import NeedsPermission, get_manager as get_perm_mgr

# CrewAI is used only for standalone multi-agent tasks (crew_manager.py),
# not for the main chat pipeline. Main chat uses direct litellm tool loop.


# ---------------------------------------------------------------------------
# RAGAgent
# ---------------------------------------------------------------------------

class RAGAgent:
    """RAG Agent powered by CrewAI — retrieve → rerank → generate via CrewAI crew."""

    def __init__(
        self,
        retriever: Retriever,
        skill_loader: Optional[SkillLoader] = None,
        plugin_loader: Optional[PluginLoader] = None,
        reranker: Optional[Reranker] = None,
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.api_base = settings.llm_api_base

        self.tools: List[ToolDef] = []
        self.tools.extend(create_filesystem_tools())
        if skill_loader:
            self.tools.extend(create_skill_tools(skill_loader))
        if plugin_loader:
            self.tools.extend(create_plugin_tools(plugin_loader))
        self.tools.append(create_delegate_tool())
        self.tools.append(create_delegate_crew_tool())
        self._delegate_depth: int = 0

        self.system_prompt = build_system_prompt_no_kb(
            skill_loader or SkillLoader(""),
            plugin_loader or PluginLoader(""),
            include_filesystem=True,
        )

        # No more LangGraph — tools are called directly via litellm

    # -- SSE event helper ---------------------------------------------------

    def _push_event(self, state: dict, event: dict):
        state.setdefault("steps", []).append(event)
        eq = state.get("_event_queue")
        if eq:
            eq.put_nowait(event)

    # -- RAG pre-processing (unchanged) ------------------------------------

    async def _retrieve(self, state: dict) -> dict:
        start = tmod.time()
        self._push_event(state, {"type": "step_start", "step_id": "retrieve", "name": "检索中", "status": "running"})

        if self.retriever.is_empty or not state.get("use_vector_db", True):
            reason = "知识库为空" if self.retriever.is_empty else "已禁用向量检索"
            dur = (tmod.time() - start) * 1000
            self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": reason, "duration_ms": round(dur, 1)})
            return {"context": [], "sources": []}

        import functools
        results = await asyncio.to_thread(
            functools.partial(self.retriever.invoke, state["question"], k=5)
        )
        context = []
        sources = []
        for doc, score in results:
            meta = doc["metadata"]
            context.append({"content": doc["text"], "metadata": meta})
            sources.append({
                "document_id": meta.get("document_id", ""),
                "content": doc["text"][:300],
                "score": score,
            })
        dur = (tmod.time() - start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": f"找到 {len(results)} 个相关片段", "duration_ms": round(dur, 1)})
        return {"context": context, "sources": sources}

    async def _rerank(self, state: dict) -> dict:
        if not self.reranker or not state.get("context"):
            if state.get("context"):
                self._push_event(state, {"type": "step_end", "step_id": "rerank", "name": "相关性重排序", "status": "completed", "detail": "重排序已禁用"})
            return {}
        start = tmod.time()
        self._push_event(state, {"type": "step_start", "step_id": "rerank", "name": "相关性重排序", "status": "running"})
        import functools
        reranked = await asyncio.to_thread(
            functools.partial(self.reranker.rerank, query=state["question"], documents=state["context"], top_k=3)
        )
        context = [{"content": doc["content"], "metadata": doc["metadata"]} for doc, _ in reranked]
        dur = (tmod.time() - start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "rerank", "name": "相关性重排序", "status": "completed", "detail": f"筛选出 {len(reranked)} 个最相关片段", "duration_ms": round(dur, 1)})
        return {"context": context}

    # -- System prompts -----------------------------------------------------

    def _system_prompt_with_kb(self) -> str:
        return (
            "You are a knowledgeable AI assistant with access to a knowledge base."
            "\n\nUse the retrieved context below to answer the user's question."
            "\n- Cite sources using [Source 1], [Source 2], etc."
            "\n- If a source has 'chapter_title' in its metadata, use that exact title when referring to the chapter."
            "\n- If a source has 'chapter_summary', it is a chapter overview — use it to describe the chapter's content."
            "\n- If you don't have enough information, say so."
            "\n\nYou have access to built-in filesystem tools (tool_ls, tool_read_file, tool_write_file, tool_edit_file, tool_glob, tool_grep, tool_execute) for reading/writing files and running shell commands."
            "\nYou also have access to skill tools (load_skill_*), plugin tools, tool_delegate_task, and tool_delegate_crew."
            "\n- Use tool_delegate_task to delegate sub-tasks to a sub-agent for parallel execution or independent analysis."
            "\n- Use tool_delegate_crew to spawn a multi-agent team (researcher/analyst/writer/coordinator) for complex tasks."
            "\n- Multiple delegate_task calls in the same round run in parallel."
            "\nIf the user asks to create/edit/manipulate documents (Word, PDF, PPT, Excel), generate visual designs, build web pages, or use other specialized capabilities, call the relevant skill or plugin tool to get instructions first."
            "\n\nCharacter Analysis (for novels, scripts, or documents with dialogues):"
            "\n- plugin_character-analysis_tool_list_characters(): List all characters and their dialogue counts."
            "\n- plugin_character-analysis_tool_get_character_dialogues(character_name, limit): Get all dialogues spoken by a character."
            "\n- plugin_character-analysis_tool_analyze_character_interactions(character_name): Find characters who appear in same chapters."
        )

    def _build_tool_defs(self) -> Optional[List[dict]]:
        if not self.tools:
            return None
        return [t.to_openai_tool() for t in self.tools]

    # -- System prompts -----------------------------------------------------

    async def _execute_tool(self, name: str, args: dict, state: Optional[dict] = None) -> str:
        if name == "tool_delegate_task":
            return await self._execute_delegate(args, state)
        if name == "tool_delegate_crew":
            return await self._execute_delegate_crew(args, state)

        for t in self.tools:
            if t.name == name:
                try:
                    eq = state.get("_event_queue") if state else None
                    if eq and name == "tool_execute":
                        try:
                            return await self._execute_tool_streaming(args, eq)
                        except NeedsPermission:
                            raise
                        except Exception as e:
                            logger.warning("tool_execute streaming failed, falling back to sync: %s", e)
                            from app.tools.filesystem import tool_execute as _sync_execute
                            result = await asyncio.to_thread(_sync_execute, **args)
                            return str(result)
                    result = await asyncio.to_thread(t.fn, **args)
                    return str(result)
                except NeedsPermission as e:
                    mgr = get_perm_mgr()
                    req = mgr.create_request(e.path, e.operation, name, args)
                    if state:
                        eq = state.get("_event_queue")
                        if eq:
                            eq.put_nowait({
                                "type": "permission_request",
                                "request_id": req.id,
                                "path": e.path,
                                "operation": e.operation,
                                "tool_name": name,
                                "tool_args": args,
                            })
                    decision = await mgr.await_decision(req.id)
                    if decision == "allowed":
                        mgr.add_temp_approval(e.path)
                        if eq and name == "tool_execute":
                            try:
                                return await self._execute_tool_streaming(args, eq)
                            except NeedsPermission:
                                pass
                            except Exception as e2:
                                logger.warning("tool_execute streaming failed on retry, falling back to sync: %s", e2)
                        result = await asyncio.to_thread(t.fn, **args)
                        return str(result)
                    return f"Permission denied: {e.path}"
                except Exception as e:
                    return f"Error executing {name}: {e}"
        return f"Tool '{name}' not found"

    async def _execute_delegate(self, args: dict, state: Optional[dict] = None) -> str:
        """Execute a delegated sub-task by spawning a SubAgent."""
        name = args.get("name", "unnamed")
        instruction = args.get("instruction", "")
        context = args.get("context", "")
        model_override = args.get("model", "")
        temperature = args.get("temperature", 0.1)
        max_tool_rounds = args.get("max_tool_rounds", 20)

        # Prevent infinite nesting
        max_depth = 3
        self._delegate_depth = getattr(self, "_delegate_depth", 0)
        if self._delegate_depth >= max_depth:
            return f"Error: max delegate depth ({max_depth}) reached"

        eq = state.get("_event_queue") if state else None

        # Resolve model (with auto-prefixing)
        model = model_override or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        # Build sub-agent: same tools (minus delegate_task to prevent recursion)
        sub_tools = [t for t in self.tools if t.name != "tool_delegate_task"]

        # Build a focused system prompt
        system_prompt = (
            "You are a focused sub-agent executing a delegated task. "
            "Complete the task below using the available tools. "
            "Return a clear, complete result to the parent agent."
        )

        sub = SubAgent(
            name=name,
            system_prompt=system_prompt,
            tools=sub_tools,
            model=model,
            api_key=self.api_key,
            api_base=self.api_base,
            temperature=temperature,
            max_tokens=4096,
            max_tool_rounds=max_tool_rounds,
        )

        # Emit start event
        if eq:
            self._push_event(state, {
                "type": "step_start",
                "step_id": f"delegate_{name}",
                "name": f"子任务: {name}",
                "status": "running",
                "subagent_name": name,
                "subagent_model": model,
            })

        self._delegate_depth += 1
        try:
            result = await sub.run(
                task=instruction,
                context=context,
                event_queue=eq,
            )
        except Exception as e:
            logger.exception("subagent '%s' failed", name)
            if eq:
                self._push_event(state, {
                    "type": "step_end",
                    "step_id": f"delegate_{name}",
                    "name": f"子任务: {name}",
                    "status": "failed",
                    "detail": str(e),
                })
            return f"Sub-agent '{name}' failed: {e}"
        finally:
            self._delegate_depth -= 1

        # Emit end event
        if eq:
            self._push_event(state, {
                "type": "step_end",
                "step_id": f"delegate_{name}",
                "name": f"子任务: {name}",
                "status": "completed",
                "detail": f"完成（{result['tool_rounds']} 轮工具调用, {result['prompt_tokens']}+{result['completion_tokens']} tokens, {result['duration_ms']}ms）",
                "duration_ms": result["duration_ms"],
                "subagent_metrics": {
                    "tool_rounds": result["tool_rounds"],
                    "prompt_tokens": result["prompt_tokens"],
                    "completion_tokens": result["completion_tokens"],
                },
            })

        return (
            f"[Sub-agent '{name}' completed]\n"
            f"Result: {result['result']}\n"
            f"Metrics: {result['tool_rounds']} tool rounds, "
            f"{result['prompt_tokens']}+{result['completion_tokens']} tokens, "
            f"{result['duration_ms']}ms"
        )

    async def _execute_delegate_crew(self, args: dict, state: Optional[dict] = None) -> str:
        """Execute a task using a CrewAI multi-agent team."""
        task_type = args.get("task_type", "research")
        topic = args.get("topic", "")
        context = args.get("context", "")
        model_override = args.get("model", "")

        eq = state.get("_event_queue") if state else None

        model = model_override or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        if eq:
            self._push_event(state, {
                "type": "step_start",
                "step_id": f"crew_{task_type}",
                "name": f"Crew: {task_type}",
                "status": "running",
                "detail": f"{topic[:100]}",
            })

        try:
            from crewai import Crew, Process
            from app.crew.agents import create_agent, AGENT_FACTORY
            from app.crew.tasks import (
                create_research_tasks,
                create_analysis_tasks,
                create_custom_tasks,
            )
            from app.crew.tools import create_crewai_tools
            from app.crew.crew_manager import TASK_CONFIGS

            config = TASK_CONFIGS.get(task_type)
            if not config:
                return f"Unknown crew task_type: {task_type}"

            # Build CrewAI-compatible tools from parent's loaders
            crew_tools = create_crewai_tools(
                plugin_loader=self.plugin_loader,
                skill_loader=self.skill_loader,
            )

            # Override LLM for all agents if model specified
            _original_llm = None
            if model_override:
                from crewai import LLM
                _original_llm = LLM
                # Monkey-patch: not needed; we pass llm to each agent below

            agents = {}
            for role in config["agents"]:
                agents[role] = create_agent(role, crew_tools)
            agent_list = [agents[r] for r in config["agents"]]

            input_data = {"topic": topic, "query": topic, "data_description": topic}
            if context:
                input_data["context"] = context

            if "researcher" in agents and "writer" in agents and len(agent_list) == 2:
                tasks = create_research_tasks(topic, agents["researcher"], agents["writer"])
            elif "analyst" in agents and "writer" in agents and len(agent_list) == 2:
                tasks = create_analysis_tasks(topic, agents["analyst"], agents["writer"])
            else:
                from app.crew.crew_manager import CrewManager
                cm = CrewManager()
                tasks = cm._create_generic_tasks(agent_list, input_data)

            crew = Crew(
                agents=agent_list,
                tasks=tasks,
                process=config["process"],
                manager_agent=agents.get("coordinator"),
                verbose=True,
            )

            result = await asyncio.to_thread(
                lambda: crew.kickoff(inputs=input_data)
            )

            raw_output = result.raw if hasattr(result, "raw") else str(result)

            if eq:
                self._push_event(state, {
                    "type": "step_end",
                    "step_id": f"crew_{task_type}",
                    "name": f"Crew: {task_type}",
                    "status": "completed",
                    "detail": f"团队协作完成",
                    "duration_ms": 0,
                })

            return (
                f"[Crew '{task_type}' completed]\n"
                f"Topic: {topic}\n"
                f"Agents: {', '.join(config['agents'])}\n"
                f"Result:\n{raw_output}"
            )

        except ImportError as e:
            return f"CrewAI is not installed: {e}"
        except Exception as e:
            logger.exception("crew task '%s' failed", task_type)
            if eq:
                self._push_event(state, {
                    "type": "step_end",
                    "step_id": f"crew_{task_type}",
                    "name": f"Crew: {task_type}",
                    "status": "failed",
                    "detail": str(e)[:200],
                })
            return f"Crew task '{task_type}' failed: {e}"

    async def _execute_tool_streaming(self, args: dict, event_queue: asyncio.Queue) -> str:
        command = args.get("command", "")
        timeout = min(args.get("timeout", 300), 600)
        work_dir = args.get("work_dir", ".")

        resolved_cwd = Path(work_dir)
        if not resolved_cwd.is_absolute():
            resolved_cwd = Path(WORKSPACE) / resolved_cwd
        resolved_cwd = resolved_cwd.resolve()

        from app.permission import get_manager as _get_perm_mgr, NeedsPermission as _NeedsPermission
        mgr = _get_perm_mgr()
        decision = mgr.check(str(resolved_cwd), "execute")
        if decision == "deny":
            return f"Error: access denied to directory '{work_dir}'"
        if decision == "ask":
            raise _NeedsPermission(str(resolved_cwd), "execute", "tool_execute", args)

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        start_time = tmod.time()
        last_heartbeat = tmod.time()

        async def _push(event: dict):
            event_queue.put_nowait(event)

        def _read_pipe(pipe, storage: list[str], source: str):
            nonlocal last_heartbeat
            for raw_line in iter(pipe.readline, b""):
                now = tmod.time()
                if now - last_heartbeat >= 5:
                    last_heartbeat = now
                    try:
                        event_queue.put_nowait({
                            "type": "tool_heartbeat",
                            "tool_name": "tool_execute",
                            "elapsed_seconds": int(now - start_time),
                            "command": command[:200],
                        })
                    except Exception:
                        pass
                decoded = raw_line.decode("utf-8", errors="replace").rstrip()
                storage.append(decoded)
                try:
                    event_queue.put_nowait({
                        "type": "tool_output",
                        "tool_name": "tool_execute",
                        "source": source,
                        "line": decoded,
                        "elapsed_seconds": int(tmod.time() - start_time),
                    })
                except Exception:
                    pass
            pipe.close()

        try:
            process = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(resolved_cwd) if resolved_cwd.is_dir() else None,
            )
        except Exception as e:
            return f"Error starting command: {e}"

        t_out = threading.Thread(target=_read_pipe, args=(process.stdout, stdout_lines, "stdout"), daemon=True)
        t_err = threading.Thread(target=_read_pipe, args=(process.stderr, stderr_lines, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        try:
            await asyncio.to_thread(process.wait, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            timed_out = True

        t_out.join(timeout=2)
        t_err.join(timeout=2)

        if timed_out:
            return f"Error: command timed out after {timeout}s"

        parts = []
        if stdout_lines:
            parts.append("\n".join(stdout_lines))
        if stderr_lines:
            parts.append(f"[stderr]\n" + "\n".join(stderr_lines))
        output = "\n".join(parts)
        rc = process.returncode or 0
        header = f"Exit code: {rc}"
        return f"{header}\n{output}" if output else header

    # -- LLM call (direct, for TaskRunner continuation) ---------------------

    async def _llm_call(self, model: str, messages: list, tool_defs: list | None) -> litellm.ModelResponse:
        start = tmod.time()
        kwargs: dict = dict(
            model=model,
            messages=messages,
            api_key=self.api_key,
            api_base=self.api_base,
            temperature=0.1,
            max_tokens=4096,
            timeout=500,
            num_retries=2,
        )
        if tool_defs:
            kwargs["tools"] = tool_defs

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            dur = (tmod.time() - start) * 1000
            record_model_call(model, duration_ms=dur)
            raise

        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
        logger.info("LLM call | model=%s pt=%d ct=%d dur=%.0fms", model, pt, ct, dur)
        return response

    # -- Generate: direct litellm tool loop (proven reliable) ----------------

    async def _generate(self, state: dict) -> dict:
        """Generate answer using litellm with tool calling loop.

        Flow: LLM → tool_calls → execute → LLM → ... until no more tool calls.
        This is the proven reliable approach (same as original LangGraph version).
        """
        _gen_start = tmod.time()
        dedup = ToolResultDedup()
        from app.context.compaction import ContextCompactor
        compactor = ContextCompactor(
            model=settings.summarization_model or self.model,
            api_key=settings.summarization_api_key or settings.llm_api_key,
            api_base=settings.summarization_api_base or settings.llm_api_base,
        )
        self._push_event(state, {"type": "step_start", "step_id": "generate", "name": "生成回答", "status": "running"})

        # Build system prompt with KB context
        if state.get("context"):
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\n\n".join(context_parts)
            full_system_prompt = (
                self._system_prompt_with_kb()
                + "\n\n"
                + f"Retrieved Context:\n{context_text}"
            )
        else:
            full_system_prompt = self.system_prompt

        tool_defs = self._build_tool_defs()

        messages = [{"role": "system", "content": full_system_prompt}]
        if state.get("history"):
            messages.extend(state["history"])

        # Build user content
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": state["question"]}]
            for f in user_files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": state["question"]})

        model = state.get("model") or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        messages = _truncate_messages(messages)
        response = await self._llm_call(model, messages, tool_defs)
        msg = response.choices[0].message

        # Tool call loop
        max_tool_rounds = 50
        rounds = 0
        while msg.tool_calls and rounds < max_tool_rounds:
            rounds += 1

            # Compaction
            if compactor.should_compact(messages):
                self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
                old_count = len(messages)
                messages = await compactor.compact(messages)
                self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})

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
                    self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                    continue

                self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "running", "tool_name": tool_name, "tool_args": args})
                tool_tasks.append(self._execute_tool(tool_name, args, state))
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

            for tc in msg.tool_calls:
                tc_id = tc.id
                result_str = early_results.get(tc_id, f"Error: no result for tool call {tc_id}")
                bounded_result = bound_tool_output(result_str, tc.function.name)
                tool_name = tc.function.name
                self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": bounded_result,
                })

            messages = _truncate_messages(messages)
            response = await self._llm_call(model, messages, tool_defs)
            msg = response.choices[0].message

        record_model_call(model, duration_ms=(tmod.time() - _gen_start) * 1000, tool_rounds=rounds)

        # If tool calls remain (max rounds reached), force final answer
        if msg.tool_calls:
            logger.warning("Max tool rounds (%d) reached, forcing answer", max_tool_rounds)
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            tool_tasks = []
            tool_metas = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                self._push_event(state, {"type": "tool_start", "step_id": f"tool_{tc.function.name}", "name": f"调用工具: {tc.function.name}", "status": "running", "tool_name": tc.function.name, "tool_args": args})
                tool_tasks.append(self._execute_tool(tc.function.name, args, state))
                tool_metas.append((tc.id, tc.function.name))
            tool_results = await asyncio.gather(*tool_tasks, return_exceptions=True)
            for (tc_id, tool_name), result in zip(tool_metas, tool_results):
                if isinstance(result, Exception):
                    result = f"Error executing {tool_name}: {result}"
                bounded_result = bound_tool_output(str(result), tool_name)
                self._push_event(state, {"type": "tool_end", "step_id": f"tool_{tool_name}", "name": f"调用工具: {tool_name}", "status": "completed", "tool_name": tool_name, "tool_result": bounded_result[:500]})
                messages.append({"role": "tool", "tool_call_id": tc_id, "content": bounded_result})
            messages = _truncate_messages(messages)
            response = await self._llm_call(model, messages, tool_defs)
            msg = response.choices[0].message

        if not (msg.content or "").strip():
            msg.content = "任务已完成，请查看结果。"

        answer = msg.content or ""
        gen_dur = (tmod.time() - _gen_start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "generate", "name": "生成回答", "status": "completed", "detail": f"完成（{rounds} 轮工具调用）" if rounds else "完成", "duration_ms": round(gen_dur, 1)})
        return {"answer": answer}

    # -- Public interface ---------------------------------------------------

    def refresh_tools(self):
        """Refresh tools list and system prompt."""
        self.tools = []
        self.tools.extend(create_filesystem_tools())
        if self.skill_loader:
            self.tools.extend(create_skill_tools(self.skill_loader))
        if self.plugin_loader:
            self.tools.extend(create_plugin_tools(self.plugin_loader))
        self.tools.append(create_delegate_tool())
        self.tools.append(create_delegate_crew_tool())
        self.system_prompt = build_system_prompt_no_kb(
            self.skill_loader or SkillLoader(""),
            self.plugin_loader or PluginLoader(""),
            include_filesystem=True,
        )

    async def invoke(
        self,
        question: str,
        model: Optional[str] = None,
        history: Optional[list[dict]] = None,
        use_vector_db: bool = True,
        files: Optional[list[dict]] = None,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """Execute full RAG pipeline: retrieve → rerank → generate (via CrewAI)."""
        state: dict = {
            "question": question,
            "context": [],
            "answer": "",
            "sources": [],
            "model": model,
            "history": history or [],
            "use_vector_db": use_vector_db,
            "files": files or [],
            "steps": [],
            "_event_queue": event_queue,
        }

        # Phase 1: RAG retrieval
        retrieve_result = await self._retrieve(state)
        state["context"] = retrieve_result.get("context", [])
        state["sources"] = retrieve_result.get("sources", [])

        # Phase 2: Rerank (optional)
        if self.reranker:
            rerank_result = await self._rerank(state)
            if rerank_result.get("context"):
                state["context"] = rerank_result["context"]

        # Phase 3: Generate via CrewAI
        gen_result = await self._generate(state)
        state["answer"] = gen_result.get("answer", "")

        return {
            "answer": state["answer"],
            "sources": state["sources"],
            "steps": state["steps"],
        }
