"""节点公共设施：工具 schema 生成 + 工具调用循环。"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import logging
import re
import time
import typing
from pathlib import Path
from typing import Any, Awaitable, Callable

from cognition.tool_output import bound_tool_output
from execution.quota import get_quota
from execution.tool_registry import ToolDefinition, ToolRegistry
from execution.tools.file_tools import (
    READ_TOOLS as _FILE_READ_TOOLS,
    WRITE_TOOLS as _FILE_WRITE_TOOLS,
)
from harness.adapter import call_argument_error
from harness.budget import BudgetExceeded, BudgetLedger
from harness.file_evidence import FileEvidenceDecision, FileTaskEvidence
from harness.hooks import run_post_tool, run_pre_tool
from harness.retry import retry_async
from harness.schema import ToolSchemaCache
from harness.turn_parts import ToolPart, TurnPartStore
from models.router import TierModel, normalize_finish_reason
from observability.metrics import record_llm_call
from observability.tracing import trace_span
from utils.json_repair import (
    is_tool_call_markup,
    parse_answer_envelope,
)

from ..events import push_event
from harness.tool_history import (
    cap_tool_history,
    make_tool_call_message,
    make_tool_result_message,
)
from ..usage import empty_usage, merge_usage, usage_from_result

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

# 写文件与 shell 独占工作区；文件读/搜索共享读锁，彼此仍可并行。
_MUTATING_TOOL_NAMES = frozenset(_FILE_WRITE_TOOLS | {"shell_exec"})
_WORKSPACE_READ_TOOLS = frozenset(_FILE_READ_TOOLS)


class _AsyncRWLock:
    """Small asyncio reader-writer lock for workspace access."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False

    async def acquire_read(self) -> None:
        async with self._condition:
            while self._writer:
                await self._condition.wait()
            self._readers += 1

    async def release_read(self) -> None:
        async with self._condition:
            self._readers -= 1
            if self._readers <= 0:
                self._readers = 0
                self._condition.notify_all()

    async def acquire_write(self) -> None:
        async with self._condition:
            while self._writer or self._readers:
                await self._condition.wait()
            self._writer = True

    async def release_write(self) -> None:
        async with self._condition:
            self._writer = False
            self._condition.notify_all()

# 纠正文：小模型常把「工具调用」当纯文本正文输出（无原生 tool_calls），
# 循环误判为最终答案 → 前端看到裸 JSON（如 {"id":"call_x","type":"function",...}）。
_NUDGE_NOT_A_TOOL_CALL = (
    "上一条输出是工具调用 JSON，不是给用户的回答。"
    "请直接用纯文本给出最终答案：不要输出 JSON，不要再调用工具。"
)
_FINAL_ROUND_NUDGE = "工具调用轮数已达上限。请立即停止调用工具，直接用纯文本给出最终答案。"
# 纠正文：模型既没有 tool_calls 也没有正文（弱模型给了空 content）——若直接当答案
# 返回空串，上层 `_append_assistant` 见空即跳过持久化、SSE 也不下发 text_delta，
# 用户看到「Agent 跑完但没有任何回答」（实锤：ollama qwen2.5:3b 在工具被拒后给出空回复）。
_NUDGE_EMPTY_ANSWER = (
    "你上一条回复是空的。请直接用纯文本给出对用户问题的最终答案（不要输出 JSON，不要再调用工具）。"
)


_NUDGE_CONTINUE_LENGTH = (
    "上一条回答因输出长度限制被截断。请从中断处继续，直接给出剩余内容，不要重复已输出的部分。"
)
_CONTENT_FILTER_TEXT = "（模型输出被安全策略拦截，无法继续生成。）"
_TRUNCATION_NOTICE = "\n\n（回答因模型输出长度限制被截断。）"
_MODEL_ERROR_TEXT = "（模型调用失败，暂时无法生成回答。）"
_NO_TOOL_EVIDENCE_TEXT = (
    "我还没有实际执行列目录/读文件工具，无法确认目录或文件内容。"
    "请切换到支持工具调用的模型，或授权目标路径后重试。"
)
_NUDGE_FILE_ACTION_NOT_EXECUTED = (
    "你上一条只说明了将要读取/查看/列出，但没有实际调用工具。"
    "请现在调用 read_file、ls 或 grep 获取真实内容后再回答；不要只描述计划。"
)


def _settle_file_evidence(
    evidence: FileTaskEvidence,
    store: TurnPartStore,
    answer: str,
    *,
    can_nudge: bool,
) -> tuple[str, bool]:
    """Apply the one file-task policy at an exit boundary.

    Returns ``(answer, should_nudge)``.  A promise without a Tool Part is
    nudged once; otherwise the model's unsupported answer is replaced.
    """
    decision = evidence.decide(
        store.completed_parts(),
        answer,
        can_nudge=can_nudge,
    )
    if decision is FileEvidenceDecision.NUDGE:
        return answer, True
    if decision is FileEvidenceDecision.BLOCK:
        return _NO_TOOL_EVIDENCE_TEXT, False
    return answer, False


def _finish_reason(value: Any) -> str:
    """Accept both provider values and already-normalized canonical values."""
    raw = str(value or "stop").strip().lower().replace("-", "_")
    return normalize_finish_reason(raw)


def _json_object_candidate(text: str) -> dict[str, Any] | None:
    """正文中的 JSON 对象候选（只做形态提取，不做宽容修复）。

    归一化覆盖三种呈现：裸 JSON、markdown 代码围栏包裹、简短散文包裹
    （取第一个 `{` 到最后一个 `}` 的跨度）。解析失败返回 None，不打日志
    —— 对每个最终答案都会调用，避免日志噪音。
    """
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 8000 or "{" not in s:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL | re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    if not (s.startswith("{") and s.endswith("}")):
        a, b = s.find("{"), s.rfind("}")
        if not (0 <= a < b):
            return None
        s = s[a : b + 1]
    if not (s.startswith("{") and s.endswith("}") and len(s) <= 8000):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _looks_like_tool_call_json(text: str) -> bool:
    """正文是否实为「工具调用」而非答案文本（覆盖所有已观测模型形态）。"""
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 8000:
        return False
    # Hermes / DeepSeek DSML 等工具标记（utils.json_repair.is_tool_call_markup）
    if is_tool_call_markup(s):
        return True
    obj = _json_object_candidate(s)
    if obj is None:
        return False
    fn = obj.get("function")
    if isinstance(fn, dict):
        # 非标准形状（小模型自造）：{"function": {"function_name": "...", ...}}
        if isinstance(fn.get("function_name"), str):
            return True
        # 标准形状：{"id": ..., "type": "function", "function": {"name": ...}}
        if isinstance(fn.get("name"), str) and (
            obj.get("type") == "function" or "id" in obj
        ):
            return True
    calls = obj.get("tool_calls")
    if isinstance(calls, list) and calls:
        return True
    # 裸形状：{"name": "tool_x", "arguments": {...} | "{...}"}（无包装层）
    if isinstance(obj.get("name"), str) and "arguments" in obj:
        return True
    return False

# 高危工具审批钩子（§5.3 require_approval / §9 execution/approval.py）。
# 由 API 层（app/api/compat.py）在本回合内注入：`async (tool, args) -> bool`。
# 未注入时按「拒绝」处理：没有审批通道就不能执行 require_approval 工具，
# 否则外部路径/命令会绕过人工审批（对齐「越界必须人工审批」的安全语义）。
PermissionHook = Callable[[str, dict[str, Any]], Awaitable[bool]]
_permission_hook_var: contextvars.ContextVar[PermissionHook | None] = contextvars.ContextVar(
    "tool_permission_hook", default=None
)


def set_permission_hook(hook: PermissionHook | None) -> contextvars.Token:
    """注入本回合的工具审批钩子（随 contextvar 传播到图节点任务）。"""
    return _permission_hook_var.set(hook)


def reset_permission_hook(token: contextvars.Token) -> None:
    try:
        _permission_hook_var.reset(token)
    except ValueError:  # generator resumed/closed from another context
        _permission_hook_var.set(None)


_TEXT_BLOCK_TYPES = ("text", "input_text")
_IMAGE_BLOCK_TYPES = ("image_url", "image", "input_image")


def content_text(content: Any) -> str:
    """把消息 content 压成纯文本（分块列表 → 拼接 text 块，图片块 → `[图片]`）。

    多模态 content 是分块数组（image_url 块含 base64）。凡需要「纯文本视图」的地方
    （路由判据 / 规划取词 / 历史回显）都必须走这里 —— `str(content)` 会把 base64
    整个灌进提示词或接口响应。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in _IMAGE_BLOCK_TYPES:
                    parts.append("[图片]")
                elif block.get("type") in _TEXT_BLOCK_TYPES or "text" in block:
                    parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _strip_media_blocks(messages: list[dict]) -> tuple[list[dict], bool]:
    """移除多模态图片分块，保留文本；用于 context overflow 的一次性重试。"""
    out: list[dict] = []
    stripped = False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[Any] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    stripped = True
                    parts.append({"type": "text", "text": "[图片已移除：上下文超限]"})
                else:
                    parts.append(part)
            msg = {**msg, "content": parts}
        out.append(msg)
    return out, stripped


def _merge_history_summary(
    messages: list[dict], summary: str, recent: list[Any]
) -> list[dict]:
    """把压缩摘要合并进首条 system，再拼接 tail 原文。"""
    tail = [dict(m) for m in recent if isinstance(m, dict)]
    if not tail:
        return messages
    head = [dict(m) for m in messages[:1]] if messages and messages[0].get("role") == "system" else []
    if head:
        original = str(head[0].get("content") or "")
        head[0]["content"] = f"{original}\n\n[历史摘要]\n{summary}" if summary else original
    elif summary:
        head = [{"role": "system", "content": f"[历史摘要]\n{summary}"}]
    return head + tail


def _trim_for_overflow(messages: list[dict], keep_last: int = 6) -> list[dict]:
    """兜底裁剪：保留 system + 最近若干条，并丢弃失去配对的 tool 消息。"""
    head = [messages[0]] if messages and messages[0].get("role") == "system" else []
    tail = messages[len(head):][-keep_last:]
    valid_ids = {
        str(call.get("id") or "")
        for msg in tail
        for call in (msg.get("tool_calls") or [])
        if isinstance(call, dict)
    }
    cleaned = [
        m
        for m in tail
        if not (m.get("role") == "tool" and str(m.get("tool_call_id") or "") not in valid_ids)
    ]
    return head + cleaned


def has_image_content(content: Any) -> bool:
    """content 是否为含图片分块的多模态列表。"""
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") in _IMAGE_BLOCK_TYPES for b in content
        )
    return False


def _type_to_schema(tp: Any) -> dict[str, Any]:
    origin = typing.get_origin(tp)
    if origin is typing.Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return _type_to_schema(args[0])
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is bool:
        return {"type": "boolean"}
    return {"type": "string"}


def json_schema_for(fn: Any) -> dict[str, Any]:
    """从函数签名推导 JSON schema（扁平化，必填最少化，§7.6.4）。"""
    try:
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001
        return {"type": "object", "properties": {}}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[name] = _type_to_schema(hints.get(name, str))
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_TOOL_SCHEMA_CACHE = ToolSchemaCache()


def build_tool_defs(
    registry: ToolRegistry, scope: str | None = None
) -> list[dict[str, Any]]:
    def _build(td: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters or json_schema_for(td.fn),
            },
        }

    return _TOOL_SCHEMA_CACHE.build(registry, scope, _build)


def _fallback_text(tool_trace: list[dict[str, Any]]) -> str:
    """工具轨迹摘要（兜底答案）：模型始终没给出正文时，把已发生的工具调用/结果
    呈现给用户，避免空答案被上层静默丢弃。

    仅当轨迹里出现「被拒 / 报错 / 越界」等异常结果时才追加摘要（让用户看到真实
    原因，如写入被拒）；全部成功的普通调用沿用占位文案（保持既有语义）。
    """
    if not tool_trace:
        return ""
    signals = ("拒绝", "错误", "越界", "未授权", "error", "Error")
    abnormal = [t for t in tool_trace if any(s in str(t.get("output", "")) for s in signals)]
    if not abnormal:
        return ""
    lines = ["（模型未给出文字总结，以下是本次工具执行情况：）"]
    for t in abnormal[-4:]:
        out = str(t.get("output", ""))
        if len(out) > 200:
            out = out[:200] + "…"
        lines.append(f"- {t.get('name', '?')}: {out}")
    return "\n".join(lines)


def _describe(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(result)


_TOOL_ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "top_k": ("max_results", "num_results", "limit", "count", "k", "n"),
    "forecast_days": (
        "days",
        "forecast",
        "forecast_day",
        "num_days",
        "days_ahead",
        "day",
    ),
    "city": ("location", "place", "city_name", "location_name"),
    "query": ("q", "keyword", "keywords", "search_query", "term"),
}


def _normalize_tool_args(fn: Any, args: dict[str, Any]) -> dict[str, Any]:
    """把模型常用的参数别名映射到函数签名，并丢弃签名不接受的参数。

    不同模型/插件对同一含义的参数命名不一致：内置 `web_search` 用 `top_k`，
    插件用 `max_results`；天气用 `city`/`forecast_days`，模型常写
    `location`/`days`。归一化后再调用，避免 `unexpected keyword argument`
    把一次本可成功的工具调用变成错误回灌。
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return args

    params = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return args

    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001
        hints = {}

    normalized = dict(args)
    for target, aliases in _TOOL_ARG_ALIASES.items():
        if target in params and target not in normalized:
            for alias in aliases:
                # 别名本身也是该工具的合法参数时不劫持（例如同时有 count）。
                if alias in normalized and alias not in params:
                    normalized[target] = normalized.pop(alias)
                    break

    dropped = [key for key in normalized if key not in params]
    for key in dropped:
        normalized.pop(key, None)
    if dropped:
        logger.debug(
            "tool arg(s) %s not accepted by %s; dropped",
            dropped,
            getattr(fn, "__name__", fn),
        )

    for key, value in list(normalized.items()):
        if not isinstance(value, str):
            continue
        annotation = hints.get(key)
        try:
            if annotation is int:
                normalized[key] = int(value)
            elif annotation is float:
                normalized[key] = float(value)
            elif annotation is bool:
                normalized[key] = value.strip().lower() in {"1", "true", "yes", "on"}
        except (TypeError, ValueError):
            pass
    return normalized


_PATH_HINT_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s\"'`，。；;、)）\u4e00-\u9fff]+"
    r"|/[^\s\"'`，。；;、)）\u4e00-\u9fff]{2,})"
)
_PATH_TOOL_NAMES = frozenset({"ls", "read_file", "grep", "glob"})
_REBASE_TOOL_NAMES = _PATH_TOOL_NAMES | frozenset(
    {"write_file", "edit_file", "append_file", "delete_file", "create_directory"}
)
_PRIOR_DIR_RE = re.compile(
    r"(?:这个|该|当前|上面|刚才|此)(?:目录|文件夹|路径)|(?:目录|文件夹)下|这里"
)
_ABS_PATH_PREFIX_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")


def _last_user_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if msg.get("role") in ("user", "human"):
            return content_text(msg.get("content", ""))
    return ""


def _current_path_hint(messages: list[Any]) -> str:
    """从当前用户消息提取显式路径（弱模型常把工具参数整个漏掉）。"""
    match = _PATH_HINT_RE.search(_last_user_text(messages))
    if not match:
        return ""
    return match.group(0).rstrip(".,;:!?，。；：！？、")


def _path_anchor(messages: list[Any]) -> str:
    """最近一次用户提到过的绝对路径，用来解析「这个目录下 X」里的相对路径。"""
    for msg in reversed(messages):
        if msg.get("role") not in ("user", "human"):
            continue
        matches = _PATH_HINT_RE.findall(content_text(msg.get("content", "")))
        if matches:
            return matches[-1].rstrip(".,;:!?，。；：！？、")
    return ""


def _apply_path_hint(
    name: str,
    fn: Any,
    args: dict[str, Any],
    hint: str,
    *,
    anchor: str = "",
    rebase_relative: bool = False,
) -> dict[str, Any]:
    """模型漏传/误传 `path` 时，用用户消息里的路径上下文补齐。

    例：用户说「c:\\tool 这个文件夹有什么」，3B 模型却给出 `ls {}`；
    `ls` 默认 `.` 会去列会话临时目录，答案就会「只做了一半」。
    后续又说「这个目录下 Tesseract-OCR 文件夹有什么」时，相对路径要落到
    上一次提到的目录（`c:\\tool\\Tesseract-OCR`），而不是会话临时目录。
    """
    if name not in _REBASE_TOOL_NAMES:
        return args
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return args
    if "path" not in params:
        return args

    raw = str(args.get("path") or "").strip()
    target = ""
    if not raw:
        if name not in _PATH_TOOL_NAMES:
            return args
        target = hint or anchor
    elif rebase_relative and anchor and not _ABS_PATH_PREFIX_RE.match(raw):
        target = str(Path(anchor) / raw)
    if not target:
        return args

    filled = dict(args)
    filled["path"] = target
    logger.info("tool %s path resolved to %s from conversation context", name, target)
    return filled


async def _dispatch(
    td: ToolDefinition, args: dict[str, Any]
) -> str:
    """执行工具；同步工具放进线程池，避免阻塞事件循环。

    内置工具（`shell_exec`/`web_search`/`web_fetch`/`http_request`/文件 IO 等）都是
    同步阻塞实现，直接在协程里调用会让整个 uvicorn 事件循环停住：聊天期间其他
    API/SSE 心跳全部排队，表现为「一聊天主进程就卡死，其他功能不能用」。
    `asyncio.to_thread` 会自动复制 contextvars（权限根目录、当前 turn 等）。
    """
    try:
        fn = td.fn
        args = _normalize_tool_args(fn, args)
        if inspect.iscoroutinefunction(fn):
            result = await fn(**args)
        else:
            result = await asyncio.to_thread(fn, **args)
            if inspect.isawaitable(result):
                result = await result
        return _describe(result)
    except TypeError as e:
        return f"错误：参数不匹配（{e}）"
    except Exception as e:  # noqa: BLE001
        return f"错误：{e or repr(e)}"


def _quota_error(tc: Any, limits: Any) -> str:
    return (
        f"错误：工具 {tc.name} 超出配额"
        f"（rate_limit={limits.rate_limit}/分钟，token_budget={limits.token_budget}）"
    )


async def _execute_with_quota(
    td: ToolDefinition,
    tc: Any,
    args: dict[str, Any],
    quota: Any,
) -> str:
    """配额检查 + 执行 + token 结算，集中在一处。"""
    from cognition.token_counter import estimate_tokens

    limits = quota.limits(tc.name)
    reserved = 0
    if limits.token_budget > 0:
        reserved = estimate_tokens(json.dumps(args, ensure_ascii=False, default=str))
    if (limits.rate_limit > 0 or limits.token_budget > 0) and not quota.allow(
        tc.name, reserved
    ):
        return _quota_error(tc, limits)

    with trace_span("tool.call", tool=tc.name):
        output = await _dispatch(td, args)
    if limits.token_budget > 0:
        actual = estimate_tokens(str(output))
        if not quota.adjust(tc.name, actual - reserved):
            return _quota_error(tc, limits)
    return output


async def _execute_tool_call(
    tc: Any,
    call_id: str,
    registry: ToolRegistry,
    agent_id: str,
    *,
    sem: asyncio.Semaphore | None = None,
    workspace_lock: _AsyncRWLock | None = None,
    permission_lock: asyncio.Lock | None = None,
    path_hint: str = "",
    path_anchor: str = "",
    rebase_relative: bool = False,
    part: ToolPart | None = None,
    store: TurnPartStore | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    parsed_args, arg_error = call_argument_error(tc)
    args: dict[str, Any] = parsed_args or {}
    if part is not None:
        part.agent_id = agent_id
        part.name = tc.name
        if store is not None:
            store.start_tool(part, args)
        else:
            part.start(args)
    started = time.perf_counter()
    td = registry.get(tc.name)

    auto_listed = False

    async def _run() -> str:
        nonlocal args, auto_listed
        if td is None:
            return f"错误：未知工具 {tc.name}"
        if arg_error:
            return f"错误：{arg_error}"
        args = run_pre_tool(tc.name, args)
        args = _normalize_tool_args(td.fn, args)
        args = _apply_path_hint(
            tc.name,
            td.fn,
            args,
            path_hint,
            anchor=path_anchor,
            rebase_relative=rebase_relative,
        )
        if td.require_approval:
            hook = _permission_hook_var.get()
            if hook is None:
                approved = False
            elif permission_lock is not None:
                async with permission_lock:
                    approved = await hook(tc.name, args)
            else:
                approved = await hook(tc.name, args)
            if not approved:
                return f"已拒绝执行工具 {tc.name}（用户未授权）"
        output = await _execute_with_quota(td, tc, args, get_quota(registry))
        if (
            tc.name == "read_file"
            and str(output).startswith("错误：")
            and "是目录" in str(output)
        ):
            ls_td = registry.get("ls")
            if ls_td is not None:
                listing = await _dispatch(ls_td, {"path": args.get("path", "")})
                output = f"{output}\n\n目录内容：\n{listing}"
                auto_listed = not str(listing).startswith(("错误", "已拒绝"))
        return run_post_tool(tc.name, args, output)

    async def _guarded() -> str:
        # Writers/shell take the exclusive side; file reads/searches take the
        # shared side. Reads still overlap each other but never a mutation.
        if td is not None and workspace_lock is not None:
            if getattr(td, "mutating", False) or td.name in _MUTATING_TOOL_NAMES:
                await workspace_lock.acquire_write()
                try:
                    return await _run()
                finally:
                    await workspace_lock.release_write()
            if td.name in _WORKSPACE_READ_TOOLS:
                await workspace_lock.acquire_read()
                try:
                    return await _run()
                finally:
                    await workspace_lock.release_read()
        return await _run()

    try:
        if td is None or arg_error:
            output = await _run()
        elif sem is not None:
            async with sem:
                output = await _guarded()
        else:
            output = await _guarded()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one tool must not abort the round
        output = f"错误：{exc or repr(exc)}"

    output = bound_tool_output(str(output), tool_name=tc.name)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    failed = (td is None or str(output).startswith(("错误", "已拒绝"))) and not auto_listed
    if part is not None:
        if store is not None:
            store.finish_tool(
                part,
                output,
                duration_ms=duration_ms,
                failed=failed,
                arguments=args,
            )
        else:
            part.finish(
                output,
                duration_ms,
                failed=failed,
                arguments=args,
            )
    trace = {"name": tc.name, "args": args, "output": output}
    history = make_tool_result_message(call_id, tc.name, output)
    return output, trace, history


def _chat_result_from_stream(stream_result: Any) -> Any:
    from models.types import ChatResult, ToolCall

    calls: list[ToolCall] = []
    for call in getattr(stream_result, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        name = getattr(fn, "name", None) if fn is not None else getattr(call, "name", "")
        args = (
            getattr(fn, "arguments", "")
            if fn is not None
            else getattr(call, "arguments", "")
        )
        calls.append(
            ToolCall(
                id=str(getattr(call, "id", "") or ""),
                name=str(name or ""),
                arguments=args if isinstance(args, str) else json.dumps(args),
            )
        )
    raw_usage = getattr(stream_result, "usage", None)
    usage = {}
    if raw_usage is not None:
        if isinstance(raw_usage, dict):
            usage = raw_usage
        else:
            usage = {
                "input": getattr(raw_usage, "prompt_tokens", 0) or 0,
                "output": getattr(raw_usage, "completion_tokens", 0) or 0,
                "total": getattr(raw_usage, "total_tokens", 0) or 0,
            }
    return ChatResult(
        content=str(getattr(stream_result, "content", "") or ""),
        tool_calls=calls,
        finish_reason=getattr(stream_result, "finish_reason", "stop") or "stop",
        usage=usage,
        model="",
    )


async def _stream_step(
    adapter: Any,
    convo: list[dict[str, Any]],
    tool_defs: list[dict[str, Any]] | None,
    registry: ToolRegistry,
    agent_id: str,
    round_no: int,
    *,
    sem: asyncio.Semaphore | None = None,
    workspace_lock: _AsyncRWLock | None = None,
    permission_lock: asyncio.Lock | None = None,
    path_hint: str = "",
    path_anchor: str = "",
    rebase_relative: bool = False,
    store: TurnPartStore | None = None,
) -> tuple[Any, dict[str, tuple[Any, str, dict[str, Any], dict[str, Any]]]]:
    from harness.tool_adapter import Done, TextDelta, ToolCallReady

    early_calls: dict[str, Any] = {}
    early_tasks: dict[str, asyncio.Task] = {}
    result = None
    try:
        async for event in adapter.stream(convo, tool_defs):
            if isinstance(event, TextDelta):
                if event.text:
                    push_event(
                        {
                            "type": "text_delta",
                            "agent_id": "",
                            "delta": event.text,
                        }
                    )
            elif isinstance(event, ToolCallReady):
                call = event.call
                call_id = call.id or f"call_{round_no}_{len(early_calls)}"
                call.id = call_id
                if call_id not in early_calls:
                    early_calls[call_id] = call
                    part = (
                        store.record_tool_call(call_id, call.name, call.arguments)
                        if store is not None
                        else None
                    )
                    # Do not await here: keep consuming the model stream while the
                    # tool runs. Results are joined after the stream ends.
                    early_tasks[call_id] = asyncio.create_task(
                        _execute_tool_call(
                            call,
                            call_id,
                            registry,
                            agent_id,
                            sem=sem,
                            workspace_lock=workspace_lock,
                            permission_lock=permission_lock,
                            path_hint=path_hint,
                            path_anchor=path_anchor,
                            rebase_relative=rebase_relative,
                            part=part,
                            store=store,
                        )
                    )
            elif isinstance(event, Done):
                result = _chat_result_from_stream(event.response)
        if result is None:
            raise RuntimeError("stream finished without Done event")
        early: dict[str, tuple[Any, str, dict[str, Any], dict[str, Any]]] = {}
        if early_tasks:
            outcomes = await asyncio.gather(*early_tasks.values())
            for call_id, outcome in zip(early_tasks.keys(), outcomes):
                output, trace, history = outcome
                early[call_id] = (early_calls[call_id], output, trace, history)
        return result, early
    except BaseException:
        for task in early_tasks.values():
            task.cancel()
        raise


def _maybe_prune_tool_outputs(
    convo: list[dict[str, Any]],
    usable_tokens: int,
    session_id: str = "",
    model_id: str = "",
) -> list[dict[str, Any]]:
    """Deterministic prune before a request; no LLM, original saved to disk.

    Records are persisted as ``session_messages.data.prune`` metadata so the
    next rebuild shows the stub without touching the original content.
    """
    if usable_tokens <= 0 or not convo:
        return convo
    try:
        from config import settings

        if not getattr(settings, "compaction_prune", True):
            return convo
        from cognition.context_meter import get_meter
        from cognition.tool_output import prune_tool_outputs

        estimated = get_meter().estimate(
            convo, model_id=model_id, use_cache=True
        ).total
        if estimated < int(usable_tokens * 0.7):
            return convo
        protect = getattr(settings, "compaction_prune_protect_tokens", 40_000)
        minimum = getattr(settings, "compaction_prune_minimum_tokens", 20_000)
        records: list[dict[str, Any]] = []
        pruned = prune_tool_outputs(
            convo,
            protect_tokens=40_000 if protect is None else int(protect),
            minimum_tokens=20_000 if minimum is None else int(minimum),
            tail_turns=2,
            records=records,
        )
        if session_id and records:
            from app.session import repository

            for record in records:
                index = int(record.get("index") or 0)
                if index >= len(convo):
                    continue
                message = convo[index]
                message_id = message.get("_id") if isinstance(message, dict) else None
                if not message_id:
                    continue
                try:
                    repository.mark_tool_pruned(
                        session_id,
                        str(message_id),
                        str(record.get("path") or ""),
                        int(record.get("tokens") or 0),
                    )
                except Exception:  # noqa: BLE001 - metadata write is best-effort
                    logger.warning("persist prune metadata failed", exc_info=True)
        return pruned
    except Exception:  # noqa: BLE001 - prune 失败不阻断请求
        logger.warning("tool output prune failed", exc_info=True)
        return convo


def _authorized_roots_note() -> str:
    """把本回合已授权的额外根目录告知模型。

    `file_agent.md`/`code_agent.md` 原先写死「禁止访问工作目录之外」，导致用户
    明确给出白名单路径时模型先拒绝、根本不触发工具审批（权限层已放行但模型不调
    工具）。这里把 `set_allowed_roots` 注入的会话临时目录 / 内部数据目录 /
    工作目录白名单显式列出，让模型知道哪些外部路径可直接访问。
    """
    try:
        from execution.filesystem import current_allowed_roots

        roots = [str(p) for p in current_allowed_roots()]
    except Exception:  # noqa: BLE001
        return ""
    if not roots:
        return ""
    lines = "\n".join(f"- {r}" for r in roots)
    return (
        "<authorized_roots>\n"
        f"{lines}\n"
        "</authorized_roots>\n"
        "以上路径已由工作目录或持久白名单授权，可直接读写。"
        "其他外部路径会触发审批，请按工具返回结果继续，不要预先拒绝。"
    )


async def run_tool_loop(
    model: TierModel,
    messages: list[dict[str, Any]],
    registry: ToolRegistry,
    *,
    scope: str | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    agent_id: str = "build",
    adapter: Any | None = None,
    budget: BudgetLedger | None = None,
    stream: bool = False,
    context_usable_tokens: int = 0,
    on_compaction: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """LLM 工具调用循环，返回 {text, rounds, tool_trace, tool_history, usage}。

    模型差异全部收敛在 Harness 的 `ToolAdapter`：本循环只消费标准
    ``tool_calls``。`agent_id` 用于 part 事件归属（主 Agent = build）。
    """
    from harness.tool_adapter import get_tool_adapter

    from config import settings as _settings

    tool_adapter = adapter or get_tool_adapter(model)
    tool_defs = build_tool_defs(registry, scope)
    max_parallel_tools = max(1, int(getattr(_settings, "tool_concurrency", 10) or 10))
    convo = [dict(m) for m in messages]
    file_evidence = FileTaskEvidence.from_messages(messages)
    path_hint = _current_path_hint(messages)
    path_anchor = _path_anchor(messages)
    rebase_relative = bool(_PRIOR_DIR_RE.search(_last_user_text(messages)))
    roots_note = _authorized_roots_note()
    if roots_note and convo and convo[0].get("role") == "system":
        content = convo[0].get("content")
        if isinstance(content, str):
            convo[0]["content"] = f"{content}\n\n{roots_note}"
    try:
        model_id = model.model_name() if hasattr(model, "model_name") else ""
        if callable(model_id):
            model_id = model_id()
        model_id = str(model_id or "")
    except Exception:  # noqa: BLE001 - accounting only
        model_id = ""
    rounds = 0
    usage_total = empty_usage()
    store = TurnPartStore(agent_id, emit=push_event)
    continued_parts: list[str] = []
    last_finish = "stop"
    empty_nudges = 0
    overflow_retried = False

    while rounds < max_rounds:
        convo = _maybe_prune_tool_outputs(
            convo,
            context_usable_tokens,
            session_id=session_id,
            model_id=model_id,
        )
        if budget is not None:
            try:
                budget.ensure_available()
            except BudgetExceeded as exc:
                logger.warning("tool loop stopped by budget: %s", exc)
                push_event(
                    {
                        "type": "budget_exceeded",
                        "agent_id": agent_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                return {
                    "text": "（会话预算已用尽，已停止继续调用模型。）",
                    "rounds": rounds,
                    "tool_trace": store.tool_trace(),
                    "tool_history": cap_tool_history(store.tool_history()),
                    "usage": usage_total,
                    "finish_reason": "budget-exceeded",
                    "budget_exceeded": True,
                }
        early: dict[str, tuple[Any, str, dict[str, Any], dict[str, Any]]] = {}
        step = store.begin_step()
        # One semaphore + one mutation lock per round. Reads/searches run in
        # parallel; writes and shell_exec are serialized to protect the workspace.
        round_sem = asyncio.Semaphore(max_parallel_tools)
        workspace_lock = _AsyncRWLock()
        permission_lock = asyncio.Lock()
        try:
            if stream and getattr(tool_adapter, "can_stream", lambda: False)():
                with trace_span("llm.call", rounds=rounds):
                    result, early = await _stream_step(
                        tool_adapter,
                        convo,
                        tool_defs or None,
                        registry,
                        agent_id,
                        rounds,
                        sem=round_sem,
                        workspace_lock=workspace_lock,
                        permission_lock=permission_lock,
                        path_hint=path_hint,
                        path_anchor=path_anchor,
                        rebase_relative=rebase_relative,
                        store=store,
                    )
            else:
                with trace_span("llm.call", rounds=rounds):
                    result = await retry_async(
                        lambda: tool_adapter.complete(convo, tool_defs or None)
                    )
        except Exception as exc:  # noqa: BLE001 - 上下文超限时压缩后重试一次
            from models.context_guard import is_overflow_error

            store.abort_step()
            if overflow_retried or not is_overflow_error(exc):
                raise
            overflow_retried = True
            before = len(convo)
            tokens_before = 0
            try:
                from harness.compression import (
                    CompactionPolicy,
                    ContextCompactor,
                    _preserve_default,
                    estimate_messages,
                )

                tokens_before = estimate_messages(convo)
                keep = (
                    _preserve_default(context_usable_tokens)
                    if context_usable_tokens > 0
                    else 8_000
                )
                result_c = await ContextCompactor().compress(
                    convo,
                    model=model,
                    policy=CompactionPolicy(
                        reason="overflow",
                        threshold=1,
                        keep_tokens=keep,
                        min_gain_tokens=1_024,
                    ),
                )
                if result_c.triggered:
                    convo = _merge_history_summary(
                        convo, result_c.summary, result_c.recent
                    )
                    try:
                        name = model.model_name() if hasattr(model, "model_name") else ""
                        model_label = str(name() if callable(name) else (name or ""))
                    except Exception:  # noqa: BLE001
                        model_label = ""
                    event = {
                        "summary": result_c.summary,
                        "reason": "overflow",
                        "tail_start_id": result_c.tail_start_id,
                        "tokens_before": int(tokens_before),
                        "tokens_after": estimate_messages(convo),
                        "model": model_label,
                    }
                    if on_compaction is not None:
                        await on_compaction(event)
            except Exception:  # noqa: BLE001 - 摘要失败继续降级
                logger.warning("overflow compression failed", exc_info=True)
            if len(convo) >= before or not convo:
                convo, stripped = _strip_media_blocks(convo)
                logger.warning(
                    "context overflow: retry once after stripping media (%s)", stripped
                )
            if len(convo) >= before and before > 2:
                convo = _trim_for_overflow(convo)
            logger.warning(
                "context overflow: compressed %d -> %d messages, retrying",
                before,
                len(convo),
            )
            continue
        rounds += 1
        label = model.model_name() if hasattr(model, "model_name") else model.tier
        call_usage = usage_from_result(result, label)
        usage_total = merge_usage(usage_total, call_usage)
        if budget is not None:
            budget.charge(float(call_usage.get("cost") or 0))
        record_llm_call(
            result.model or model.tier,
            result.usage.get("input", 0),
            result.usage.get("output", 0),
        )

        last_finish = _finish_reason(result.finish_reason)

        # Reconcile streamed early calls before recording the step: a provider may
        # report ``stop`` while still having produced Tool Parts.  Parts decide.
        if early:
            early_calls = list(early.values())
            if len(early_calls) == len(result.tool_calls):
                for idx, tc in enumerate(result.tool_calls):
                    if not tc.id and early_calls[idx][0].id:
                        tc.id = early_calls[idx][0].id
            known = {tc.id for tc in result.tool_calls if tc.id}
            for call_id, (call, *_rest) in early.items():
                if call_id not in known:
                    result.tool_calls.append(call)
        for idx, tc in enumerate(result.tool_calls):
            if not getattr(tc, "id", ""):
                tc.id = f"call_{rounds}_{idx}"
        step = store.record_result(result, finish_reason=last_finish)

        if not store.should_continue(step):
            text = result.content or ""
            if last_finish == "content-filter":
                return {
                    "text": _CONTENT_FILTER_TEXT,
                    "rounds": rounds,
                    "tool_trace": store.tool_trace(),
                    "tool_history": cap_tool_history(store.tool_history()),
                    "usage": usage_total,
                    "finish_reason": last_finish,
                }
            if last_finish == "error":
                return {
                    "text": text.strip() or _MODEL_ERROR_TEXT,
                    "rounds": rounds,
                    "tool_trace": store.tool_trace(),
                    "tool_history": cap_tool_history(store.tool_history()),
                    "usage": usage_total,
                    "finish_reason": last_finish,
                }
            if last_finish == "length":
                if text.strip() and rounds < max_rounds:
                    continued_parts.append(text)
                    convo.append({"role": "assistant", "content": text})
                    convo.append(
                        {"role": "user", "content": _NUDGE_CONTINUE_LENGTH}
                    )
                    store.finish_step(step)
                    continue
                answer = "".join(continued_parts + ([text] if text else []))
                answer, _ = _settle_file_evidence(
                    file_evidence,
                    store,
                    answer,
                    can_nudge=False,
                )
                if not answer.strip():
                    answer = _NUDGE_CONTINUE_LENGTH
                return {
                    "text": answer + _TRUNCATION_NOTICE,
                    "rounds": rounds,
                    "tool_trace": store.tool_trace(),
                    "tool_history": cap_tool_history(store.tool_history()),
                    "usage": usage_total,
                    "finish_reason": last_finish,
                }
            if _looks_like_tool_call_json(text):
                if rounds < max_rounds:
                    # 工具调用被模型写成了正文（无原生 tool_calls）→ 纠正一轮，不当答案返回。
                    # 记录原始响应摘录做诊断（等价于 set_verbose 抓包，但不打印整包请求/密钥）。
                    logger.warning(
                        "模型把工具调用写成了正文（model=%s round=%d）: %.300s",
                        label,
                        rounds,
                        text,
                    )
                    convo.append({"role": "assistant", "content": text})
                    convo.append({"role": "user", "content": _NUDGE_NOT_A_TOOL_CALL})
                    store.finish_step(step)
                    continue
                store.finish_step(step)
                break  # 轮数已尽：交由下方纯文本收尾，不把裸 JSON 当最终答案
            # 弱模型把回答包进 JSON 外壳（{"response": "..."}）→ 解包归一化
            envelope = parse_answer_envelope(text)
            answer = envelope or text
            if answer.strip():
                if continued_parts:
                    answer = "".join(continued_parts) + answer
                answer, should_nudge = _settle_file_evidence(
                    file_evidence,
                    store,
                    answer,
                    can_nudge=rounds < max_rounds,
                )
                if should_nudge:
                    convo.append({"role": "assistant", "content": text})
                    convo.append(
                        {"role": "user", "content": _NUDGE_FILE_ACTION_NOT_EXECUTED}
                    )
                    store.finish_step(step)
                    continue
                return {
                    "text": answer,
                    "rounds": rounds,
                    "tool_trace": store.tool_trace(),
                    "tool_history": cap_tool_history(store.tool_history()),
                    "usage": usage_total,
                    "finish_reason": last_finish,
                }
            # 空回复（既无 tool_calls 也无正文）：不能把空串当答案返回，否则上层会
            # 静默丢弃（无持久化 / 无 text_delta），表现为「Agent 跑完却没有任何回答」。
            if rounds < max_rounds and empty_nudges < 2:
                empty_nudges += 1
                logger.warning(
                    "模型给出空回复（model=%s round=%d，已有 %d 次工具调用）→ 要求补答",
                    label,
                    rounds,
                    len(store.tool_trace()),
                )
                convo.append({"role": "assistant", "content": ""})
                convo.append({"role": "user", "content": _NUDGE_EMPTY_ANSWER})
                store.finish_step(step)
                continue
            store.finish_step(step)
            break  # 轮数已尽：交由下方纯文本收尾

        empty_nudges = 0

        round_calls = [
            (tc.id or f"call_{rounds}_{idx}", tc.name, tc.arguments)
            for idx, tc in enumerate(result.tool_calls)
        ]
        assistant_history = make_tool_call_message(round_calls, result.content or "")
        assistant_message = {
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
                for call_id, name, arguments in round_calls
            ],
        }
        convo.append(assistant_message)
        round_tasks: dict[str, asyncio.Task] = {}
        effective_args: dict[str, dict[str, Any]] = {}
        for idx, tc in enumerate(result.tool_calls):
            call_id = round_calls[idx][0]
            if call_id not in early:
                part = store.record_tool_call(call_id, tc.name, tc.arguments)
                round_tasks[call_id] = asyncio.create_task(
                    _execute_tool_call(
                        tc,
                        call_id,
                        registry,
                        agent_id,
                        sem=round_sem,
                        workspace_lock=workspace_lock,
                        permission_lock=permission_lock,
                        path_hint=path_hint,
                        path_anchor=path_anchor,
                        rebase_relative=rebase_relative,
                        part=part,
                        store=store,
                    )
                )
        try:
            for idx, tc in enumerate(result.tool_calls):
                call_id = round_calls[idx][0]
                if call_id in early:
                    _, output, trace, _history = early[call_id]
                else:
                    output, trace, _history = await round_tasks[call_id]
                if isinstance(trace.get("args"), dict):
                    effective_args[call_id] = trace["args"]
                convo.append(
                    {"role": "tool", "tool_call_id": call_id, "content": output}
                )
        except BaseException:
            for task in round_tasks.values():
                task.cancel()
            raise

        # 把实际执行的参数（路径补全/归一化后）写回 assistant 调用与持久化
        # 历史，避免下一轮/刷新后仍看到 ls({}) 这类原始参数。
        if effective_args:
            updated_calls = [
                (
                    call_id,
                    name,
                    json.dumps(effective_args[call_id], ensure_ascii=False)
                    if call_id in effective_args
                    else arguments,
                )
                for call_id, name, arguments in round_calls
            ]
            refreshed = make_tool_call_message(updated_calls, result.content or "")
            assistant_history["tool_calls"] = refreshed["tool_calls"]
            assistant_message["tool_calls"] = refreshed["tool_calls"]
            round_calls = updated_calls
        store.finish_step(step)

    _prior_nudges = {_FINAL_ROUND_NUDGE, _NUDGE_NOT_A_TOOL_CALL, _NUDGE_EMPTY_ANSWER}
    if not (convo and convo[-1].get("role") == "user" and convo[-1].get("content") in _prior_nudges):
        convo.append({"role": "user", "content": _FINAL_ROUND_NUDGE})
    convo = _maybe_prune_tool_outputs(
        convo,
        context_usable_tokens,
        session_id=session_id,
        model_id=model_id,
    )
    if budget is not None:
        try:
            budget.ensure_available()
        except BudgetExceeded as exc:
            logger.warning("final tool-loop call stopped by budget: %s", exc)
            push_event(
                {
                    "type": "budget_exceeded",
                    "agent_id": agent_id,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            return {
                "text": "（会话预算已用尽，已停止继续调用模型。）",
                "rounds": rounds,
                "tool_trace": store.tool_trace(),
                "tool_history": cap_tool_history(store.tool_history()),
                "usage": usage_total,
                "finish_reason": "budget-exceeded",
                "budget_exceeded": True,
            }
    try:
        with trace_span("llm.call", rounds=rounds, final=True):
            final = await retry_async(lambda: tool_adapter.complete(convo, None))
        rounds += 1
        label = model.model_name() if hasattr(model, "model_name") else model.tier
        final_usage = usage_from_result(final, label)
        usage_total = merge_usage(usage_total, final_usage)
        if budget is not None:
            budget.charge(float(final_usage.get("cost") or 0))
        record_llm_call(
            final.model or model.tier,
            final.usage.get("input", 0),
            final.usage.get("output", 0),
        )
        final_finish = _finish_reason(final.finish_reason)
        last_finish = final_finish
        store.finish_reason = final_finish
        final_text = (final.content or "").strip()
        if final_finish == "content-filter":
            final_text = _CONTENT_FILTER_TEXT
        elif final_finish == "error":
            final_text = final_text or _MODEL_ERROR_TEXT
        elif _looks_like_tool_call_json(final_text):
            final_text = ""
        else:
            envelope = parse_answer_envelope(final_text)
            final_text = envelope or final_text
        if final_finish == "length" and final_text:
            final_text += _TRUNCATION_NOTICE
        final_text, _ = _settle_file_evidence(
            file_evidence,
            store,
            final_text,
            can_nudge=False,
        )
        if (final_text or "").strip():
            if continued_parts:
                final_text = "".join(continued_parts) + final_text
            return {
                "text": final_text,
                "rounds": rounds,
                "tool_trace": store.tool_trace(),
                "tool_history": cap_tool_history(store.tool_history()),
                "usage": usage_total,
                "finish_reason": last_finish,
            }
    except Exception:  # noqa: BLE001 - 收尾失败仍走占位文案兜底
        logger.warning("工具轮数耗尽后的纯文本收尾调用失败", exc_info=True)

    # 最后防线：连收尾调用都没给出非空文本 —— 绝不能返回空串（上层见空即静默丢弃）。
    # 若已有工具调用，用工具轨迹摘要兜底（让用户看到「写被拒/报错」等真实原因）；
    # 否则退回既有的占位文案。
    tool_fallback = _fallback_text(store.tool_trace())
    if tool_fallback:
        fallback_text = tool_fallback
    elif empty_nudges >= 2:
        fallback_text = "（模型未产出可展示内容，请换模型或重试）"
    else:
        fallback_text = "(达到最大工具轮数，未产出最终答案)"
    fallback_text, _ = _settle_file_evidence(
        file_evidence,
        store,
        fallback_text,
        can_nudge=False,
    )
    if fallback_text == _NO_TOOL_EVIDENCE_TEXT:
        pass
    elif continued_parts and not tool_fallback:
        fallback_text = "".join(continued_parts) + _TRUNCATION_NOTICE
    return {
        "text": fallback_text,
        "rounds": rounds,
        "tool_trace": store.tool_trace(),
        "tool_history": cap_tool_history(store.tool_history()),
        "usage": usage_total,
        "finish_reason": last_finish,
    }
