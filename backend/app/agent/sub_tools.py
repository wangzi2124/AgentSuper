"""子 Agent（code/web_search）共享的工具执行器与权限桥。

背景：code/web_search 子 Agent 原先只会单轮 LLM 对话，无法读写项目文件，
能力与主 RAG Agent 不对称。这里复用 backend/app/tools/file_tools.py 的文件
工具，并遵循与主链路一致的权限模型：

- 工作区内操作直接执行（PermissionManager 判定 allow）
- 外部/未授权路径触发 NeedsPermission 时，把 permission_request 事件经
  事件队列桥接给前端（multi-agent UI 已接审批面板），随后异步等待用户
  审批结果：allowed 则临时放行并重试执行，denied/超时则以拒绝结果反馈
  LLM；无事件队列时直接拒绝（对齐 graph.py 行为）。
"""

import asyncio
import inspect
import logging
import time as tmod
from typing import Optional

import litellm

from app.agent.stream_events import emit, step_event
from app.config import settings
from app.context.token_counter import estimate_tokens
from app.context.tool_output import bound_tool_output
from app.monitor import record_model_call
from app.permission import NeedsPermission, get_manager as get_perm_mgr
from app.permission import set_session_workspace, reset_session_workspace
from app.tools import file_tools as fs
from app.utils.json_repair import parse_tool_args

logger = logging.getLogger(__name__)

# 子 Agent 工具循环最大轮数（对齐主 Agent MAX_TOOL_ROUNDS 语义，config.max_tool_rounds）
def _sub_agent_max_rounds() -> int:
    return max(2, int(settings.max_tool_rounds or 8))

# 子 Agent 可见的工具白名单（仅文件读写与搜索，不暴露插件/generator 等）
# ── 子 Agent 上下文截断:控制 tool 循环 context 膨胀 ──
_SUB_CTX_MAX_TOKENS = 18_000  # [opencode] 与主 Agent usable 上下文同一量级，避免过早裁剪失忆
_SUB_CTX_KEEP_ROUNDS = 4      # 保留最近 4 轮（对齐主 Agent tail_turns 语义）

# 达到轮数上限时的强制收尾提示（对齐主 Agent MAX_STEPS_PROMPT / opencode max-steps 语义）
MAX_STEPS_PROMPT = (
    "CRITICAL - MAXIMUM STEPS REACHED\n\n"
    "本轮已达到单次请求允许的最大步骤数，工具已禁用，请以纯文本回复。\n\n"
    "STRICT REQUIREMENTS:\n"
    "1. 不要再调用任何工具（包括读取、写入、编辑、搜索等）。\n"
    "2. 必须给出文字总结，包含：已完成的步骤/文件、尚未完成的任务、建议的下一步操作。\n"
    "3. 该约束优先于其他所有指令。"
)

# Doom-loop 检测提示（对齐主 Agent DOOM_LOOP_PROMPT）
DOOM_LOOP_PROMPT = (
    "系统提示：检测到连续多轮调用完全相同的工具参数，疑似陷入死循环。"
    "请立即停止重复调用，改变策略（例如先读取/检查，再采取不同的操作），"
    "或基于已有信息直接给出最终回答。"
)


def _trim_messages(messages: list[dict]) -> list[dict]:
    """按估算 token 裁剪 messages；按“轮”丢弃最旧内容，保持 tool_call 配对完整。"""
    # [token 优化 v4] 用真实 token 估算替代字符数：12K token ≈ 4~5 万字符，
    # 原字符数口径在中文场景把上限压到 ~3K token，过早裁剪导致子 Agent 失忆重做。
    def _size(ms: list[dict]) -> int:
        return sum(
            estimate_tokens(str(m.get("content") or "")) + estimate_tokens(str(m.get("tool_calls") or ""))
            for m in ms
        )

    if len(messages) <= 2 or _size(messages) <= _SUB_CTX_MAX_TOKENS:
        return messages

    head = messages[:2]  # system + user 永远保留
    tail = messages[2:]
    recent: list[dict] = []
    rounds = 0
    i = len(tail) - 1
    while i >= 0 and rounds < _SUB_CTX_KEEP_ROUNDS:
        m = tail[i]
        recent.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            rounds += 1
        i -= 1
    recent.reverse()
    trimmed = head + recent
    # 极端兜底:仍超限则去掉 tool 消息(此时已不依赖 tool_call 配对)
    if _size(trimmed) > _SUB_CTX_MAX_TOKENS:
        trimmed = [m for m in trimmed if m.get("role") != "tool"]
    return trimmed


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
    "tool_apply_patch",
)

_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "tool_ls",
            "description": "列出指定目录下的文件和子目录，显示类型、大小和修改时间（被 .gitignore 忽略的项不列出）。",
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
            "description": "读取文本文件内容（cat -n 格式返回，含行号；超长行截断），支持按行偏移和行数限制；多模态文件（图片/PDF/音视频）返回 base64。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "起始行号（从 1 开始）"},
                    "limit": {"type": "integer", "description": "读取行数上限，默认 2000，0 表示默认 2000；大文件请用 offset 分页续读"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_glob",
            "description": "在指定目录中按 glob 模式搜索文件名，返回匹配路径列表（按修改时间排序，最多 100 条）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式，如 '**/*.py'"},
                    "path": {"type": "string", "description": "搜索根目录（相对工作区）"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_grep",
            "description": "在文本文件中按正则表达式搜索内容，支持文件过滤、上下文显示与只列文件名（最多 100 条匹配）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "include": {"type": "string", "description": "文件过滤 glob，如 '*.py'"},
                    "context": {"type": "integer", "description": "匹配行前后显示行数"},
                    "files_only": {"type": "boolean", "description": "只输出文件名"},
                    "path": {"type": "string", "description": "搜索根目录（相对工作区）"},
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
            "description": "在文件中查找并替换指定字符串。支持模糊匹配；old_string 匹配到多处时会报错（除非 replace_all=True 全部替换）。",
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
            "description": "在工作区内执行白名单 shell 命令（如 python/node/git）。支持管道/重定向/&& 等 shell 语义，每个命令段都会做白名单校验，默认 300s 超时（上限 600s）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "超时秒数（上限 600）"},
                    "workdir": {"type": "string", "description": "工作目录（相对工作区）"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_apply_patch",
            "description": "应用 apply_patch 格式的补丁（对齐 opencode apply_patch 工具），支持 Add File / Update File / Delete File 三种操作。适合一次创建/修改/删除多个文件；单处替换用 tool_edit_file 更简单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch_text": {"type": "string", "description": "补丁文本。格式：以 '*** Begin Patch' 开头，'*** End Patch' 结尾；'*** Add File: <path>' 后跟以 '+' 前缀的内容行；'*** Update File: <path>' 后跟以 '-'/'+'/' ' 前缀的行（@@ 头部可选）；'*** Delete File: <path>' 无需内容。"},
                },
                "required": ["patch_text"],
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
        return fs.unwrap(await asyncio.to_thread(fn, **_coerce_args(fn, args)))
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
            return fs.unwrap(await asyncio.to_thread(fn, **_coerce_args(fn, args)))
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
    history: Optional[list[dict]] = None,
    directory: str = "",
) -> str:
    """子 Agent 的 LLM 工具循环：允许读写文件/搜索/执行白名单命令，最后返回文本回答。

    对齐 opencode 主循环语义（config.max_tool_rounds / MAX_STEPS / doom-loop）：
      - 工具调用并行执行（asyncio.gather）
      - 连续相同工具指纹 ≥3 轮注入策略变更提示（doom-loop）
      - 达到轮数上限的最后一次调用禁用工具并注入收尾提示，强制结构化总结
      - history 注入为前置对话；directory 作为本请求文件作用域（会话工作目录）

    每轮工具调用前先请求一次模型；模型返回工具调用则执行并回填结果，返回纯文本
    则结束。达到最大轮数后强制做一次无工具收尾调用。
    """
    model = model or settings.llm_model
    api_key = api_key or settings.llm_api_key
    api_base = api_base or settings.llm_api_base
    max_rounds = _sub_agent_max_rounds()

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
    ]
    if history:
        for h in history:
            if not isinstance(h, dict):
                continue
            role = h.get("role")
            content = h.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)})
    messages.append({"role": "user", "content": user_message})

    def _llm_call(with_tools: bool, max_tokens: int = 4096) -> dict:
        kwargs: dict = {
            "model": model,
            "api_key": api_key,
            "api_base": api_base,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "cache_prompt": True
        }
        if with_tools:
            kwargs["tools"] = _TOOL_SCHEMAS
            kwargs["tool_choice"] = "auto"
        return kwargs

    async def _exec_one(tc) -> tuple[str, str]:
        """执行单个工具调用（权限桥 + 事件上报），返回 (tool_call_id, result)。"""
        name = tc.function.name
        args = parse_tool_args(tc.function.arguments)
        if args is None:
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
        return tc.id, result

    doom_fingerprints: list[str] = []
    ws_token = set_session_workspace(directory) if directory else None
    try:
        for rnd in range(1, max_rounds + 1):
            messages[:] = _trim_messages(messages)  # 每轮前裁剪,防 context 无限膨胀
            use_tools = rnd < max_rounds
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

            # 并行执行全部工具调用（对齐主 Agent asyncio.gather）
            results = await asyncio.gather(*[_exec_one(tc) for tc in tool_calls], return_exceptions=True)
            for (tc_id, result), tc in zip(results, tool_calls):
                if isinstance(result, Exception):
                    result = f"Error executing {tc.function.name}: {result}"
                # 与主 Agent 一致：入口截断 + 超限写盘（data/truncation/）+ 续读提示，
                # 避免大文件读取把子 Agent 上下文撑爆且截断后无法续读（对齐 opencode truncate.ts）
                bounded = bound_tool_output(str(result), tc.function.name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": bounded,
                })

            # Doom-loop 检测：连续相同指纹 ≥3 轮 → 注入策略变更提示（对齐主 Agent）
            fp = "|".join(
                sorted(f"{tc.function.name}:{tc.function.arguments}" for tc in tool_calls)
            )
            doom_fingerprints.append(fp)
            if len(doom_fingerprints) >= 3 and len(set(doom_fingerprints[-3:])) == 1:
                logger.warning("Sub-agent doom loop detected (%s), injecting strategy prompt", fp[:120])
                messages.append({"role": "user", "content": DOOM_LOOP_PROMPT})
                doom_fingerprints.clear()

        # 达到最大轮数：注入收尾提示并禁用工具强制总结（对齐 MAX_STEPS 语义）
        messages.append({"role": "user", "content": MAX_STEPS_PROMPT})
        response = await litellm.acompletion(**_llm_call(False, max_tokens=2048))
        return (response.choices[0].message.content or "").strip()
    finally:
        if ws_token is not None:
            reset_session_workspace(ws_token)
