"""子 Agent（code/web_search）共享的工具执行器与权限桥。

背景：code/web_search 子 Agent 原先只会单轮 LLM 对话，无法读写项目文件，
能力与主 RAG Agent 不对称。这里复用 backend/app/tools/filesystem.py 的文件
工具，并遵循与主链路一致的权限模型：

- 工作区内操作直接执行（PermissionManager 判定 allow）
- 外部/未授权路径触发 NeedsPermission 时，把 permission_request 事件经
  事件队列桥接给前端（multi-agent UI 已接审批面板），随后异步等待用户
  审批结果：allowed 则临时放行并重试执行，denied/超时则以拒绝结果反馈
  LLM；无事件队列时直接拒绝（对齐 graph.py 行为）。
"""

import asyncio
import inspect
import json
import logging
import time as tmod
from typing import Optional

import litellm

from app.agent.stream_events import emit, step_event
from app.config import settings
from app.monitor import record_model_call
from app.permission import NeedsPermission, get_manager as get_perm_mgr
from app.tools import filesystem as fs

logger = logging.getLogger(__name__)

# 子 Agent 工具循环最大轮数（含最终无工具收尾调用前的最多执行轮）
SUB_AGENT_MAX_ROUNDS = 8

# 工具结果回传 LLM 时的截断长度，避免 context 膨胀
_TOOL_RESULT_TRUNC = 4000

# 子 Agent 可见的工具白名单（仅文件读写与搜索，不暴露插件/generator 等）
_AVAILABLE_TOOLS = (
    "tool_ls",
    "tool_read_file",
    "tool_glob",
    "tool_grep",
    "tool_write_file",
    "tool_append_file",
    "tool_edit_file",
    "tool_delete_file",
    "tool_rename_file",
    "tool_execute",
)

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "tool_ls",
            "description": "列出指定目录下的文件和子目录，显示类型、大小和修改时间。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "目录路径（相对工作区，默认当前目录）"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_read_file",
            "description": "读取文本文件内容，支持按行偏移和行数限制；多模态文件（图片/PDF/音视频）返回 base64。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "起始行号（从 1 开始）"},
                    "limit": {"type": "integer", "description": "读取行数，0 表示全部"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_glob",
            "description": "在指定目录中按 glob 模式搜索文件名，返回匹配路径列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式，如 '**/*.py'"},
                    "root": {"type": "string", "description": "搜索根目录（相对工作区）"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_grep",
            "description": "在文本文件中按正则表达式搜索内容，支持文件过滤、上下文显示与只列文件名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "include": {"type": "string", "description": "文件过滤 glob，如 '*.py'"},
                    "context": {"type": "integer", "description": "匹配行前后显示行数"},
                    "files_only": {"type": "boolean", "description": "只输出文件名"},
                    "root": {"type": "string", "description": "搜索根目录（相对工作区）"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_write_file",
            "description": "创建新文件并写入内容。overwrite=True 时允许覆盖已存在文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_append_file",
            "description": "向文件追加内容（文件不存在则创建），用于分段写入大文件。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_edit_file",
            "description": "在文件中查找并替换指定字符串，支持单次或全部替换。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_delete_file",
            "description": "删除指定文件或空目录（仅限工作区内）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_rename_file",
            "description": "重命名或移动文件/目录到新路径。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "new_path": {"type": "string"}},
                "required": ["path", "new_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_execute",
            "description": "在工作区内执行白名单 shell 命令（如 python/node/git），有 120s 超时限制。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "超时秒数（上限 600）"},
                    "work_dir": {"type": "string", "description": "工作目录（相对工作区）"},
                },
                "required": ["command"],
            },
        },
    },
]


def _coerce_args(fn, args: dict) -> dict:
    """过滤掉 LLM 可能多传、而函数签名不接受的参数，避免 TypeError。"""
    params = set(inspect.signature(fn).parameters)
    params.discard("self")
    return {k: v for k, v in args.items() if k in params}


def _permission_denied_msg(operation: str, path: str) -> str:
    return (
        f"Permission denied: {operation} on '{path}' was not approved (user denied or request timed out). "
        "Only paths inside the workspace are accessible without approval. "
        "Adjust to a workspace path and retry, or explain the limitation to the user."
    )


async def run_tool(name: str, args: dict, event_queue=None) -> str:
    """执行子 Agent 工具调用，处理权限桥与错误归一化。"""
    fn = getattr(fs, name, None)
    if fn is None:
        return f"Error: unknown tool '{name}'"
    try:
        return str(await asyncio.to_thread(fn, **_coerce_args(fn, args)))
    except NeedsPermission as e:
        mgr = get_perm_mgr()
        req = mgr.create_request(e.path, e.operation, name, args)
        emit(event_queue, {
            "type": "permission_request",
            "request_id": req.id,
            "path": e.path,
            "operation": e.operation,
            "tool_name": name,
            "tool_args": args,
        })
        if not event_queue:
            # 无事件队列（脱离请求流）时无人能审批，直接拒绝而不是永久等待
            logger.warning("Permission request denied: no event queue to approve %s", e.path)
            return _permission_denied_msg(e.operation, e.path)
        decision = await mgr.await_decision(req.id)
        if decision == "allowed":
            mgr.add_temp_approval(e.path)
            return str(await asyncio.to_thread(fn, **_coerce_args(fn, args)))
        return _permission_denied_msg(e.operation, e.path)
    except Exception as e:
        return f"Error executing {name}: {e}"


async def tool_loop_chat(
    system_prompt: str,
    user_message: str,
    event_queue=None,
    agent_id: str = "",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
) -> str:
    """子 Agent 的 LLM 工具循环：允许读写文件/搜索/执行白名单命令，最后返回文本回答。

    每轮工具调用前先请求一次模型；模型返回工具调用则执行并回填结果，返回纯文本
    则结束。达到最大轮数后强制做一次无工具收尾调用。
    """
    model = model or settings.llm_model
    api_key = api_key or settings.llm_api_key
    api_base = api_base or settings.llm_api_base

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    def _llm_call(with_tools: bool, max_tokens: int = 4096) -> dict:
        kwargs: dict = {
            "model": model,
            "api_key": api_key,
            "api_base": api_base,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        if with_tools:
            kwargs["tools"] = _TOOL_SCHEMAS
            kwargs["tool_choice"] = "auto"
        return kwargs

    for rnd in range(1, SUB_AGENT_MAX_ROUNDS + 1):
        use_tools = rnd < SUB_AGENT_MAX_ROUNDS
        start = tmod.time()
        response = await litellm.acompletion(**_llm_call(use_tools))
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=(tmod.time() - start) * 1000)

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        content = (msg.content or "").strip()
        if not tool_calls:
            return content or "(无回答)"

        # 记录本轮 assistant 消息（含全部工具调用）
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except Exception:
                args = {}
                logger.warning("Sub-agent malformed tool args for %s", name)

            step_id = f"tool_{rnd}_{tc.id[:8]}"
            emit(event_queue, {
                "type": "agent_step",
                "agent_id": agent_id,
                "step": step_event(step_id, name, "running", tool_name=name, tool_args=args),
            })
            result = await run_tool(name, args, event_queue)
            emit(event_queue, {
                "type": "agent_step",
                "agent_id": agent_id,
                "step": step_event(
                    step_id, name, "completed",
                    detail=result[:200].replace("\n", " "),
                    tool_name=name, tool_args=args,
                ),
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result[:_TOOL_RESULT_TRUNC],
            })

    # 达到最大轮数：禁用工具强制收尾
    response = await litellm.acompletion(**_llm_call(False, max_tokens=2048))
    return (response.choices[0].message.content or "").strip()
