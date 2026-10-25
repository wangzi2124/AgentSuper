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
from typing import Any, Awaitable, Callable

from cognition.tool_output import bound_tool_output, prune_tool_outputs
from execution.tool_registry import ToolDefinition, ToolRegistry
from harness.adapter import call_argument_error
from harness.budget import BudgetExceeded, BudgetLedger
from harness.hooks import run_post_tool, run_pre_tool
from harness.retry import retry_async
from harness.schema import ToolSchemaCache
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
_FILE_TASK_RE = re.compile(
    r"(?:列出|查看|看看|看|显示|读取|打开|read|ls|dir|find|grep|glob)"
    r".{0,24}(?:文件|目录|文件夹|路径|盘|[\\w./-]+\\.\\w{1,8})"
    r"|(?:文件|目录|文件夹|盘).{0,24}(?:有哪些|有什么|列表|内容|结构)",
    re.IGNORECASE,
)


def _needs_tool_evidence(messages: list[Any]) -> bool:
    for msg in reversed(messages):
        if msg.get("role") in ("user", "human"):
            text = content_text(msg.get("content", ""))
            return bool(_FILE_TASK_RE.search(text))
    return False


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
    _permission_hook_var.reset(token)


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


async def _dispatch(
    td: ToolDefinition, args: dict[str, Any]
) -> str:
    try:
        result = td.fn(**args)
        if inspect.isawaitable(result):
            result = await result
        return _describe(result)
    except TypeError as e:
        return f"错误：参数不匹配（{e}）"
    except Exception as e:  # noqa: BLE001
        return f"错误：{e or repr(e)}"


async def _execute_tool_call(
    tc: Any,
    call_id: str,
    registry: ToolRegistry,
    agent_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    parsed_args, arg_error = call_argument_error(tc)
    args: dict[str, Any] = parsed_args or {}
    step_id = f"tool-{call_id}"
    push_event(
        {
            "type": "tool_start",
            "step_id": step_id,
            "agent_id": agent_id,
            "name": tc.name,
            "tool_name": tc.name,
            "tool_args": args,
            "status": "running",
        }
    )
    started = time.perf_counter()
    td = registry.get(tc.name)
    if td is None:
        output = f"错误：未知工具 {tc.name}"
    elif arg_error:
        output = f"错误：{arg_error}"
    else:
        args = run_pre_tool(tc.name, args)
        if td.require_approval:
            hook = _permission_hook_var.get()
            approved = await hook(tc.name, args) if hook is not None else False
            if not approved:
                output = f"已拒绝执行工具 {tc.name}（用户未授权）"
            else:
                with trace_span("tool.call", tool=tc.name):
                    output = await _dispatch(td, args)
        else:
            with trace_span("tool.call", tool=tc.name):
                output = await _dispatch(td, args)
        output = run_post_tool(tc.name, args, output)

    output = bound_tool_output(str(output), tool_name=tc.name)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    failed = td is None or str(output).startswith(("错误", "已拒绝"))
    push_event(
        {
            "type": "tool_end",
            "step_id": step_id,
            "agent_id": agent_id,
            "name": tc.name,
            "tool_name": tc.name,
            "tool_result": output,
            "status": "failed" if failed else "completed",
            "duration_ms": duration_ms,
        }
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
) -> tuple[Any, dict[str, tuple[Any, str, dict[str, Any], dict[str, Any]]]]:
    from harness.tool_adapter import Done, ToolCallReady

    early: dict[str, tuple[Any, str, dict[str, Any], dict[str, Any]]] = {}
    result = None
    async for event in adapter.stream(convo, tool_defs):
        if isinstance(event, ToolCallReady):
            call = event.call
            call_id = call.id or f"call_{round_no}_{len(early)}"
            call.id = call_id
            if call_id not in early:
                output, trace, history = await _execute_tool_call(
                    call, call_id, registry, agent_id
                )
                early[call_id] = (call, output, trace, history)
        elif isinstance(event, Done):
            result = _chat_result_from_stream(event.response)
    if result is None:
        raise RuntimeError("stream finished without Done event")
    return result, early


def _authorized_roots_note() -> str:
    """把本回合已授权的额外根目录告知模型。

    `file_agent.md`/`code_agent.md` 原先写死「禁止访问工作目录之外」，导致用户
    明确给出白名单路径时模型先拒绝、根本不触发工具审批（权限层已放行但模型不调
    工具）。这里把 `set_allowed_roots` 注入的会话目录 / 额外工作区 / 持久白名单
    显式列出，让模型知道哪些外部路径可直接访问。
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
) -> dict[str, Any]:
    """LLM 工具调用循环，返回 {text, rounds, tool_trace, tool_history, usage}。

    模型差异全部收敛在 Harness 的 `ToolAdapter`：本循环只消费标准
    ``tool_calls``。`agent_id` 用于 part 事件归属（主 Agent = build）。
    """
    from harness.tool_adapter import get_tool_adapter

    tool_adapter = adapter or get_tool_adapter(model)
    tool_defs = build_tool_defs(registry, scope)
    convo = [dict(m) for m in messages]
    requires_tool_evidence = _needs_tool_evidence(messages)
    roots_note = _authorized_roots_note()
    if roots_note and convo and convo[0].get("role") == "system":
        content = convo[0].get("content")
        if isinstance(content, str):
            convo[0]["content"] = f"{content}\n\n{roots_note}"
    rounds = 0
    usage_total = empty_usage()
    tool_trace: list[dict[str, Any]] = []
    tool_history: list[dict[str, Any]] = []
    continued_parts: list[str] = []
    last_finish = "stop"
    empty_nudges = 0

    while rounds < max_rounds:
        if rounds:
            convo = prune_tool_outputs(convo)
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
                    "tool_trace": tool_trace,
                    "tool_history": cap_tool_history(tool_history),
                    "usage": usage_total,
                    "finish_reason": "budget-exceeded",
                    "budget_exceeded": True,
                }
        early: dict[str, tuple[Any, str, dict[str, Any], dict[str, Any]]] = {}
        if (
            stream
            and getattr(tool_adapter, "mode", "") == "fallback"
            and getattr(tool_adapter, "can_stream", lambda: False)()
        ):
            with trace_span("llm.call", rounds=rounds):
                result, early = await _stream_step(
                    tool_adapter,
                    convo,
                    tool_defs or None,
                    registry,
                    agent_id,
                    rounds,
                )
        else:
            with trace_span("llm.call", rounds=rounds):
                result = await retry_async(
                    lambda: tool_adapter.complete(convo, tool_defs or None)
                )
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

        if not result.tool_calls:
            text = result.content or ""
            if last_finish == "content-filter":
                return {
                    "text": _CONTENT_FILTER_TEXT,
                    "rounds": rounds,
                    "tool_trace": tool_trace,
                    "tool_history": cap_tool_history(tool_history),
                    "usage": usage_total,
                    "finish_reason": last_finish,
                }
            if last_finish == "error":
                return {
                    "text": text.strip() or _MODEL_ERROR_TEXT,
                    "rounds": rounds,
                    "tool_trace": tool_trace,
                    "tool_history": cap_tool_history(tool_history),
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
                    continue
                answer = "".join(continued_parts + ([text] if text else []))
                if requires_tool_evidence and not tool_trace:
                    answer = _NO_TOOL_EVIDENCE_TEXT
                if not answer.strip():
                    answer = _NUDGE_CONTINUE_LENGTH
                return {
                    "text": answer + _TRUNCATION_NOTICE,
                    "rounds": rounds,
                    "tool_trace": tool_trace,
                    "tool_history": cap_tool_history(tool_history),
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
                    continue
                break  # 轮数已尽：交由下方纯文本收尾，不把裸 JSON 当最终答案
            # 弱模型把回答包进 JSON 外壳（{"response": "..."}）→ 解包归一化
            envelope = parse_answer_envelope(text)
            answer = envelope or text
            if answer.strip():
                if continued_parts:
                    answer = "".join(continued_parts) + answer
                if requires_tool_evidence and not tool_trace:
                    answer = _NO_TOOL_EVIDENCE_TEXT
                return {
                    "text": answer,
                    "rounds": rounds,
                    "tool_trace": tool_trace,
                    "tool_history": cap_tool_history(tool_history),
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
                    len(tool_trace),
                )
                convo.append({"role": "assistant", "content": ""})
                convo.append({"role": "user", "content": _NUDGE_EMPTY_ANSWER})
                continue
            break  # 轮数已尽：交由下方纯文本收尾

        empty_nudges = 0
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

        round_calls = [
            (tc.id or f"call_{rounds}_{idx}", tc.name, tc.arguments)
            for idx, tc in enumerate(result.tool_calls)
        ]
        tool_history.append(make_tool_call_message(round_calls, result.content or ""))
        convo.append(
            {
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
        )
        for idx, tc in enumerate(result.tool_calls):
            call_id = round_calls[idx][0]
            if call_id in early:
                _, output, trace, history = early[call_id]
            else:
                output, trace, history = await _execute_tool_call(
                    tc, call_id, registry, agent_id
                )
            tool_trace.append(trace)
            tool_history.append(history)
            convo.append(
                {"role": "tool", "tool_call_id": call_id, "content": output}
            )

    _prior_nudges = {_FINAL_ROUND_NUDGE, _NUDGE_NOT_A_TOOL_CALL, _NUDGE_EMPTY_ANSWER}
    if not (convo and convo[-1].get("role") == "user" and convo[-1].get("content") in _prior_nudges):
        convo.append({"role": "user", "content": _FINAL_ROUND_NUDGE})
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
                "tool_trace": tool_trace,
                "tool_history": cap_tool_history(tool_history),
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
        if requires_tool_evidence and not tool_trace:
            final_text = _NO_TOOL_EVIDENCE_TEXT
        if (final_text or "").strip():
            if continued_parts:
                final_text = "".join(continued_parts) + final_text
            return {
                "text": final_text,
                "rounds": rounds,
                "tool_trace": tool_trace,
                "tool_history": cap_tool_history(tool_history),
                "usage": usage_total,
                "finish_reason": last_finish,
            }
    except Exception:  # noqa: BLE001 - 收尾失败仍走占位文案兜底
        logger.warning("工具轮数耗尽后的纯文本收尾调用失败", exc_info=True)

    # 最后防线：连收尾调用都没给出非空文本 —— 绝不能返回空串（上层见空即静默丢弃）。
    # 若已有工具调用，用工具轨迹摘要兜底（让用户看到「写被拒/报错」等真实原因）；
    # 否则退回既有的占位文案。
    tool_fallback = _fallback_text(tool_trace)
    if tool_fallback:
        fallback_text = tool_fallback
    elif empty_nudges >= 2:
        fallback_text = "（模型未产出可展示内容，请换模型或重试）"
    else:
        fallback_text = "(达到最大工具轮数，未产出最终答案)"
    if requires_tool_evidence and not tool_trace:
        fallback_text = _NO_TOOL_EVIDENCE_TEXT
    elif continued_parts and not tool_fallback:
        fallback_text = "".join(continued_parts) + _TRUNCATION_NOTICE
    return {
        "text": fallback_text,
        "rounds": rounds,
        "tool_trace": tool_trace,
        "tool_history": cap_tool_history(tool_history),
        "usage": usage_total,
        "finish_reason": last_finish,
    }
