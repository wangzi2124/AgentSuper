"""上下文压缩（对齐 agent.md §4.3）。

- 阈值触发（默认 80K token），保留最近 N token 原文，仅对更早的历史做增量摘要。
- 9 段式结构化摘要模板（prompts/nodes/compress.md）。
- 摘要生成后附自检清单，确保关键信息（路径/命令/数字/约束）未丢失。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

from prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

COMPRESS_THRESHOLD = 80_000
KEEP_RECENT_TOKENS = 20_000
TOOL_OUTPUT_MAX_CHARS = 2000

SELF_CHECKLIST = (
    "自检：① 用户最初诉求 ② 已达成/未达成 ③ 关键约束与偏好 "
    "④ 涉及的文件路径与命令 ⑤ 关键数字/参数 ⑥ 错误与修复 "
    "⑦ 待办与下一步 ⑧ 重要决策 ⑨ 时间线"
)

_ROLE_LABELS = {
    "human": "User",
    "user": "User",
    "ai": "Assistant",
    "assistant": "Assistant",
    "system": "System",
    "tool": "Tool result",
}


def estimate_tokens(text: str) -> int:
    """CJK 加权 token 估算（≈1.5 tok/CJK 字符，其余 ≈chars/4）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 1.5 + other / 4) + 1


def _msg_text(m: Any) -> str:
    if isinstance(m, str):
        return m
    content = getattr(m, "content", None)
    if content is None and isinstance(m, dict):
        content = m.get("content", "")
    return str(content or "")


def _msg_role(m: Any) -> str:
    role = getattr(m, "type", None)
    if role is None and isinstance(m, dict):
        role = m.get("role") or m.get("type")
    return _ROLE_LABELS.get(str(role), "User")


def serialize_messages(messages: Sequence[Any]) -> str:
    """序列化为带角色标记的纯文本（工具结果截断），供摘要使用。"""
    lines: list[str] = []
    for m in messages:
        role = _msg_role(m)
        text = _msg_text(m)[:TOOL_OUTPUT_MAX_CHARS] if role in ("Tool result", "tool") else _msg_text(m)
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            names = ", ".join(getattr(tc, "name", "") for tc in tool_calls)
            lines.append(f"[{role}/tool call] {names}")
        if text:
            lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def split_by_token(
    messages: Sequence[Any], keep_tokens: int = KEEP_RECENT_TOKENS
) -> tuple[list[Any], list[Any]]:
    """从尾部向前累计 token，切出「最近 keep_tokens 原文」与「更早历史」。"""
    msgs = list(messages)
    keep: list[Any] = []
    used = 0
    idx = len(msgs)
    while idx > 0:
        cost = estimate_tokens(serialize_messages([msgs[idx - 1]]))
        if used + cost > keep_tokens and keep:
            break
        used += cost
        keep.insert(0, msgs[idx - 1])
        idx -= 1
    return msgs[:idx], keep


def select_tail(
    messages: Sequence[dict],
    *,
    budget: int,
    tail_turns: int,
    estimate: Callable[[Sequence[dict]], int],
) -> tuple[list[dict], list[dict]]:
    """把消息切成 (head, tail)：head 待压缩、tail 原样保留。

    - 以 user 消息划分轮次，保留最后 `tail_turns` 轮（受 `budget` 约束）。
    - 超预算的轮次在「轮次边界」（user 开头或完整工具轮起点）处切开，
      只保留最近部分，避免切断 tool_calls ↔ tool 的对应关系。
    - 分割后若尾部不以 user 开头，则把最新的 user 问题补到尾部开头，
      保证下一轮 LLM 调用仍有用户消息锚定。

    `estimate(messages) -> int` 由调用方注入，使各栈沿用各自的 token 口径。
    """
    if not messages:
        return messages, []

    user_idx = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idx:
        return messages, []

    boundaries = sorted({
        0,
        *user_idx,
        *(i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")),
    })
    n = len(user_idx)
    ends = user_idx[1:] + [len(messages)]

    keep = None
    total = 0
    for j in range(n - 1, -1, -1):
        if n - j > tail_turns:
            break
        start, end = user_idx[j], ends[j]
        size = estimate(messages[start:end])
        if total + size <= budget:
            total += size
            keep = start
            continue
        remaining = max(0, budget - total)
        split = _split_suffix(messages, start, end, boundaries, remaining, estimate)
        if split is not None:
            keep = split
        break

    if keep is None:
        # 预算完全放不下（含分割失败）→ 兜底把最新一整轮作为尾部。
        if len(messages) <= 1:
            return messages, []
        last_user = user_idx[-1]
        if last_user == 0:
            return messages, []
        return messages[:last_user], messages[last_user:]

    if keep == 0:
        # 整个对话都在保留预算内 → 没有更旧的内容可压缩
        return [], messages

    tail = messages[keep:]
    # 分割路径的尾部不以 user 开头 → 补上最新的 user 问题（体积很小）
    if tail and tail[0].get("role") != "user":
        nu = user_idx[-1]
        if nu < keep:
            tail = [messages[nu]] + tail
    return messages[:keep], tail


def _split_suffix(
    messages: Sequence[dict],
    start: int,
    end: int,
    boundaries: Sequence[int],
    remaining: int,
    estimate: Callable[[Sequence[dict]], int],
) -> int | None:
    """在 (start, end) 内找最大的轮次边界 b，使 messages[b:end] 落在剩余预算内。"""
    for b in reversed(boundaries):
        if start < b < end:
            if estimate(messages[b:end]) <= remaining:
                return b
    return None


def build_compress_prompt(existing_summary: str, serialized: str) -> str:
    """用 9 段式模板渲染压缩提示词。"""
    body = f"<previous-summary>\n{existing_summary or '（无）'}\n</previous-summary>\n\n{serialized}"
    return PromptLoader().load("nodes/compress.md", content=body)


async def compress_context(
    messages: Sequence[Any],
    existing_summary: str = "",
    model: Any | None = None,
    threshold: int = COMPRESS_THRESHOLD,
    keep_tokens: int = KEEP_RECENT_TOKENS,
) -> dict[str, Any]:
    """按阈值触发增量压缩，返回 {summary, recent, triggered, tokens}。"""
    msgs = list(messages)
    total = sum(estimate_tokens(serialize_messages([m])) for m in msgs)

    if total <= threshold:
        return {"summary": existing_summary, "recent": msgs, "triggered": False, "tokens": total}

    older, recent = split_by_token(msgs, keep_tokens)
    if not older:
        return {"summary": existing_summary, "recent": recent, "triggered": False, "tokens": total}

    if model is None:
        from models.router import get_model

        model = get_model("simple")

    prompt = build_compress_prompt(existing_summary, serialize_messages(older))
    try:
        result = await model.ainvoke([{"role": "user", "content": prompt}])
        summary = str(getattr(result, "content", "") or "").strip() or existing_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("compress LLM call failed, keeping existing summary: %s", exc)
        summary = existing_summary

    return {"summary": summary, "recent": recent, "triggered": True, "tokens": total}
