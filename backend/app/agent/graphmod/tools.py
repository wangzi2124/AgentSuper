"""拆分模块 `tools`（含 RAGAgentTools）。

原文件 docstring: (无)"""
# ── 复制自原模块的顶层 import ──
import asyncio

import inspect

import logging

import os

import shlex

import subprocess

import threading

import time as tmod

import uuid

from collections.abc import Sequence

from pathlib import Path

from typing import Annotated, Callable, TypedDict

from app.context.token_counter import truncate_messages as _truncate_messages

from app.context.token_counter import sanitize_tool_messages

from app.context.tool_output import bound_tool_output, prune_tool_outputs

from app.context.tool_dedup import ToolResultDedup

from app.context.budget import usable_context_tokens, compaction_threshold_tokens, prune_protect_tokens, prune_minimum_tokens

from app.utils.json_repair import parse_tool_args

import litellm

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from langgraph.graph import StateGraph, END

from app.agent.base import AgentMessage

from app.rag.retriever import Retriever

from app.rag.reranker import Reranker

from app.skills.loader import SkillLoader

from app.plugins.loader import PluginLoader

from app.config import settings

from app.agent.tools import (
    ToolDef,
    LONG_CONTENT_FILE_RULE,
    create_filesystem_tools,
    create_skill_tools,
    create_plugin_tools,
    build_system_prompt_no_kb,
)

from app.skills.custom_tools import CustomToolStore  # [token 优化 v6]

from app.monitor import record_model_call

from app.trace_log import trace, trace_messages  # [token trace v7]

from app.prompt_log import log_prompt  # [prompt log v1]

from app.permission import NeedsPermission, get_manager as get_perm_mgr
from .base import RAGAgentBase
# ── 跨子模块依赖（自动生成）──
from .constants import _TASK_TOOL_SUBAGENTS
from .constants import _is_multi_agent_queue
from .constants import _permission_denied_msg
from .state import AgentState
logger = logging.getLogger(__name__)
# ── 类分块（verbatim，继承链切片）──
class RAGAgentTools(RAGAgentBase):
    def _task_tool_placeholder(self, description: str = "", prompt: str = "", subagent_type: str = "web_search") -> str:
        """占位实现：_execute_tool 对 tool_task 特判，这里不会被真正调用。"""
        return ""
    def _memory_tool_placeholder(self, **kwargs) -> str:
        """占位实现：_execute_tool 对 tool_memory_* 特判，这里不会被真正调用。"""
        return ""
    async def _tool_task(self, args: dict, depth: int = 0, event_queue=None, directory: str = "", conversation_id: str = "") -> str:
        """[opencode task tool] 把聚焦子任务委派给子 Agent 并取回最终结果。

        - subagent_type 白名单排除 rag（= 自身）与 supervisor（编排者非执行者），防自递归
        - 嵌套深度受 settings.subagent_depth 限制（默认 1 = 主 Agent 只能再委派一层，对齐 opencode）
        - 子 Agent 以全新上下文执行（对齐 opencode task 工具 "fresh context" 语义），
          事件队列仅当来自 multi-agent 流时才透传（单 Agent 流不推子 Agent 面板事件）；
          会话工作目录（directory）透传给子 Agent，文件工具落在会话目录而非 git worktree
        - conversation_id 透传外层会话 id，保证子 Agent 与主 Agent 落在同一记忆 namespace
          （否则子 Agent 记忆读写落在全局 namespace，与主 Agent 会话隔离记忆互不可见）
        """
        prompt = str(args.get("prompt") or "").strip()
        subagent_type = str(args.get("subagent_type") or "").strip()
        if not prompt:
            return "Error: 'prompt' is required for tool_task."
        if subagent_type not in _TASK_TOOL_SUBAGENTS:
            return f"Error: unknown subagent_type '{subagent_type}'. Valid: {sorted(_TASK_TOOL_SUBAGENTS)}."
        bus = getattr(self, "task_bus", None)
        if bus is None:
            return "Error: sub-agent bus is unavailable (tool_task disabled)."
        max_depth = max(1, settings.subagent_depth)
        if depth >= max_depth:
            return (
                f"Error: sub-agent depth limit reached ({max_depth}). "
                f"Increase SUBAGENT_DEPTH (default 1) to allow nested sub-agents."
            )

        # 工具密集型子 Agent 使用更长等待，避免长任务被误判超时（对齐 supervisor._timeout_for）
        timeout = settings.sub_agent_timeout_extended if subagent_type == "code" else settings.sub_agent_timeout
        sub_thread_id = f"task:{uuid.uuid4().hex[:8]}"
        reply = await bus.send_and_wait(
            AgentMessage(
                source="user",
                target=subagent_type,
                type="request",
                action="chat",
                payload={
                    "question": prompt,
                    "model": self.model,
                    "history": [],
                    "use_vector_db": False,
                    "files": [],
                    "conversation_id": conversation_id,
                    "directory": directory,
                    "_task_depth": depth + 1,
                    "_event_queue": event_queue,
                },
                thread_id=sub_thread_id,
            ),
            timeout=timeout,
        )
        if reply.type == "response":
            return reply.payload.get("answer", "") or "(no answer)"
        err = reply.payload.get("error", "sub-agent failed")
        return f"Error: sub-agent '{subagent_type}' failed: {err}"
    async def _tool_memory(self, name: str, args: dict, state: AgentState | None) -> str:
        """[opencode memory] 主 Agent 记忆读写：set/get/search。

        基于共享 MemoryManager（runtime 注入），会话内按 conversation_id 隔离。
        与子 Agent 共用同一实例，因此主 Agent 记住的信息子 Agent 也可检索到
        （对齐 opencode 的共享上下文语义）。
        """
        mm = getattr(self, "memory", None)
        if mm is None:
            return "Error: memory is unavailable."
        namespace = str((state or {}).get("conversation_id") or "")

        try:
            if name == "tool_memory_set":
                key = str(args.get("key") or "").strip()
                value = args.get("value")
                if not key:
                    return "Error: 'key' is required."
                tags = args.get("tags") or []
                if not isinstance(tags, list):
                    tags = [str(tags)] if tags else []
                tags = [str(t) for t in tags if str(t).strip()]
                await mm.set(key, value, ttl=settings.memory_ttl_seconds, tags=tags, namespace=namespace)
                return f"记住成功: key='{key}' (会话内有效, 标签: {tags or '无'})"
            if name == "tool_memory_get":
                key = str(args.get("key") or "").strip()
                if not key:
                    return "Error: 'key' is required."
                val = await mm.get(key, default=None, namespace=namespace)
                if val is None:
                    return f"未找到 key='{key}'（可能未记住或已过期）"
                return f"{key}: {val}"
            if name == "tool_memory_search":
                tag = str(args.get("tag") or "").strip()
                if not tag:
                    return "Error: 'tag' is required."
                found = await mm.get_by_tag(tag, namespace=namespace)
                if not found:
                    return f"未找到标签 '{tag}' 相关的记忆"
                lines = [f"{k}: {v}" for k, v in found.items()]
                return f"标签 '{tag}' 相关记忆:\n" + "\n".join(lines)
            return f"Error: unknown memory tool '{name}'"
        except Exception as e:
            logger.warning("Memory tool %s failed: %s", name, e)
            return f"Error executing {name}: {e}"
    async def _execute_tool(self, name: str, args: dict, state: dict | None = None) -> str:
        """执行指定的工具函数，处理权限检查和错误。"""
        for t in self.tools:
            if t.name == name:
                try:
                    # [opencode task tool] 主 Agent 自主委派子 Agent 的入口（注入任务深度 + 事件队列）
                    if name == "tool_task":
                        depth = int((state or {}).get("_task_depth", 0) or 0)
                        eq = (state or {}).get("_event_queue")
                        if not _is_multi_agent_queue(eq):
                            eq = None  # 单 Agent 流不推子 Agent 面板事件
                        else:
                            # 剥掉委派方自己的 TaggedEventQueue，让子 Agent 用自己的 id 重新打标签
                            from app.agent.stream_events import unwrap_tagged
                            eq = unwrap_tagged(eq)
                        return await self._tool_task(args, depth=depth, event_queue=eq,
                                                     directory=(state or {}).get("_cwd", ""),
                                                     conversation_id=str((state or {}).get("conversation_id") or ""))
                    if name in ("tool_memory_set", "tool_memory_get", "tool_memory_search"):
                        return await self._tool_memory(name, args, state)
                    eq = state.get("_event_queue") if state else None
                    if eq and name == "tool_execute":
                        try:
                            return await self._execute_tool_streaming(args, eq, state.get("_on_activity"))
                        except NeedsPermission:
                            raise
                        except Exception as e:
                            logger.warning("tool_execute streaming failed, falling back to sync: %s", e)
                            from app.tools.file_tools import tool_execute as _sync_execute, unwrap
                            result = await asyncio.to_thread(_sync_execute, **args)
                            return unwrap(result)
                    if inspect.iscoroutinefunction(t.fn):
                        result = await t.fn(**args)
                    else:
                        result = await asyncio.to_thread(t.fn, **args)
                    return self._bound_plugin_result(name, result)
                except NeedsPermission as e:
                    mgr = get_perm_mgr()
                    req = mgr.create_request(e.path, e.operation, name, args)
                    eq = state.get("_event_queue") if state else None
                    if eq:
                        eq.put_nowait({
                            "type": "permission_request",
                            "request_id": req.id,
                            "path": e.path,
                            "operation": e.operation,
                            "tool_name": name,
                            "tool_args": args,
                        })
                    if not eq:
                        # 无事件队列（如多 agent 总线路径）时无人能审批，
                        # 直接拒绝而不是永久等待（此前会卡到 supervisor 超时）
                        logger.warning("Permission request denied: no event queue to approve %s", e.path)
                        return _permission_denied_msg(e.operation, e.path, name)
                    decision = await mgr.await_decision(req.id)
                    if decision == "allowed":
                        if e.operation == "command":
                            mgr.add_temp_command_approval(e.path)
                        else:
                            mgr.add_temp_approval(e.path)
                        try:
                            if eq and name == "tool_execute":
                                try:
                                    return await self._execute_tool_streaming(args, eq, state.get("_on_activity"))
                                except NeedsPermission:
                                    pass
                                except Exception as e2:
                                    logger.warning("tool_execute streaming failed on retry, falling back: %s", e2)
                            if inspect.iscoroutinefunction(t.fn):
                                result = await t.fn(**args)
                            else:
                                result = await asyncio.to_thread(t.fn, **args)
                            return self._bound_plugin_result(name, result)
                        except NeedsPermission as e2:
                            # [C5 修复] 审批放行后的重试仍被拒 → 返回拒绝消息而非向调用方
                            # 抛裸异常（修复前在 except 处理器内再抛，绕过外层 except Exception）。
                            logger.warning("Permission retry still denied: %s", e2.path)
                            return _permission_denied_msg(e2.operation, e2.path, name)
                    return _permission_denied_msg(e.operation, e.path, name)
                except Exception as e:
                    return f"Error executing {name}: {e}"
        return f"Tool '{name}' not found"
    async def _execute_tool_streaming(self, args: dict, event_queue: asyncio.Queue, on_activity: Callable[[str], None] | None = None) -> str:
        """流式执行shell命令，实时推送输出到事件队列。

        对齐 opencode shell.ts 的单路径语义：Popen 启动子进程 + 后台线程读流，
        经 loop.call_soon_threadsafe 把行推回事件队列——不依赖事件循环自身的
        子进程能力（Windows + uvicorn --reload 下事件循环为 SelectorEventLoop，
        asyncio.create_subprocess_* 会抛 NotImplementedError）。
        """
        command = args.get("command", "")
        timeout = min(args.get("timeout", 300), 600)
        if timeout < 1:
            timeout = 5
        work_dir = args.get("workdir") or args.get("work_dir") or "."

        from app.tools.file_tools import _resolve as _fs_resolve, _kill_process_tree as _kill_tree
        resolved_cwd = _fs_resolve(work_dir)

        from app.permission import get_manager as _get_perm_mgr, NeedsPermission as _NeedsPermission
        mgr = _get_perm_mgr()
        decision = mgr.check(str(resolved_cwd), "execute")
        if decision == "deny":
            return f"Error: access denied to directory '{work_dir}'"
        if decision == "ask":
            raise _NeedsPermission(str(resolved_cwd), "execute", "tool_execute", args)

        # Apply whitelist check (same as filesystem.tool_execute)
        from app.tools.file_tools import _validate_shell_command, _needs_shell
        try:
            _validate_shell_command(command, cwd=str(resolved_cwd), ask=True)
        except ValueError as e:
            return f"Error: {e}"

        # 检查写重定向目标文件权限（>、>> 等），防止 shell 命令绕过文件工具的权限检查
        from app.tools.file_tools import _check_redirect_targets_permission
        try:
            _check_redirect_targets_permission(command, resolved_cwd)
        except (_NeedsPermission, PermissionError):
            raise
        except Exception:
            pass

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        start_time = tmod.time()
        hb_state = {"last": start_time}
        loop = asyncio.get_running_loop()

        def _push(ev: dict) -> None:
            """仅在事件循环线程内执行（经 call_soon_threadsafe 调度），对象非线程安全也安全。"""
            try:
                event_queue.put_nowait(ev)
            except Exception:
                pass

        def _emit_threadsafe(ev: dict) -> None:
            try:
                loop.call_soon_threadsafe(_push, ev)
            except RuntimeError:
                pass  # 循环已关闭（请求已结束/取消），静默丢弃

        def _pump(pipe, storage: list[str], source: str) -> None:
            """工作线程：逐行读管道，落 storage 并推送 tool_output/心跳事件（与旧异步版同语义）。"""
            from app.tools.file_tools import decode_process_output
            try:
                for raw in iter(pipe.readline, b""):
                    decoded = decode_process_output(raw).rstrip()
                    storage.append(decoded)
                    now = tmod.time()
                    if now - hb_state["last"] >= 5:
                        hb_state["last"] = now
                        _emit_threadsafe({
                            "type": "tool_heartbeat",
                            "tool_name": "tool_execute",
                            "elapsed_seconds": int(now - start_time),
                            "command": command[:200],
                        })
                        if on_activity:
                            try:
                                on_activity(f"tool_execute 运行中 ({int(now - start_time)}s)")
                            except Exception:
                                pass
                    _emit_threadsafe({
                        "type": "tool_output",
                        "tool_name": "tool_execute",
                        "source": source,
                        "line": decoded,
                        "elapsed_seconds": int(tmod.time() - start_time),
                    })
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        # 无 shell 语义 → exec 防注入；否则走真实 shell（与 tool_execute 对齐）
        popen_kwargs: dict = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            if _needs_shell(command):
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(resolved_cwd) if resolved_cwd.is_dir() else None,
                    **popen_kwargs,
                )
            else:
                process = subprocess.Popen(
                    shlex.split(command),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(resolved_cwd) if resolved_cwd.is_dir() else None,
                )
        except Exception as e:
            detail = str(e) or repr(e) or type(e).__name__
            return (
                f"Error starting command: {detail}\n"
                f"Command: {command!r}\n"
                f"Workdir: {str(resolved_cwd) if resolved_cwd.is_dir() else '.'}"
            )

        readers = [
            threading.Thread(target=_pump, args=(process.stdout, stdout_lines, "stdout"), daemon=True),
            threading.Thread(target=_pump, args=(process.stderr, stderr_lines, "stderr"), daemon=True),
        ]
        for th in readers:
            th.start()

        # 事件驱动等待：process.wait() 在线程里阻塞（不依赖事件循环的子进程协程），
        # wait_for 提供超时。相比旧版 poll()+sleep 轮询无 0.2s 延迟地板，
        # 恢复旧实现的即时返回；超时取消后杀树，等待线程随进程退出自然回收。
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            # 同步杀整树（taskkill /T /F）：不用 _async_kill_process_tree——
            # 其内部同样走 create_subprocess_exec，selector 循环下会静默失败
            await asyncio.to_thread(_kill_tree, process)
            await asyncio.to_thread(process.wait)
            return f"Error: command timed out after {timeout}s"

        def _join_all() -> None:
            for th in readers:
                th.join(5)

        # 命令已执行完毕，此处绝不能再抛异常（否则调用方会回退同步路径造成二次执行）
        try:
            await asyncio.wait_for(asyncio.to_thread(_join_all), timeout=12)
        except Exception:
            pass

        parts = []
        if stdout_lines:
            parts.append("\n".join(stdout_lines))
        if stderr_lines:
            parts.append(f"[stderr]\n" + "\n".join(stderr_lines))
        output = "\n".join(parts)
        rc = process.returncode or 0
        header = f"Exit code: {rc}"
        if output:
            result = f"{header}\n{output}"
        else:
            result = header
        from app.tools.file_tools import append_cmd_dialect_hint
        return append_cmd_dialect_hint(result)

__all__ = ['RAGAgentTools']
