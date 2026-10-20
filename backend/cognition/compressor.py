"""上下文压缩（对齐 agent.md §4.3）。

- 阈值触发（默认 80K token），保留最近 N token 原文，仅对更早的历史做增量摘要。
- 9 段式结构化摘要模板（prompts/nodes/compress.md）。
- 摘要生成后附自检清单，确保关键信息（路径/命令/数字/约束）未丢失。
"""

from __future__ import annotations

import logging
import re
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


# ── LLM 摘要门禁 + 精确值回填（agent-1.md §1.4 / §1.5）──
# 原则：压缩是「低成本高收益」，可积极启用，但**每次调模型都要过门禁**；
# 越不过门禁就退回规则截断（不花一分钱）。摘要生成后用纯规则确定性地回填精确值，
# 不依赖模型「记得住」。

# 精确值模式（§1.5 PRECISION_PATTERNS）：文件路径 / 长数字（ID、端口）/ 反引号命令。
PRECISION_PATTERNS = [
    r"[/\\][\w./\\-]+\.\w{2,4}",   # 文件路径（含 Windows 反斜杠）
    r"\b\d{4,}\b",                 # 长数字（ID、端口、token 计数）
    r"`[^`]+`",                    # 反引号命令
]
# 门禁 C2 的精确值关键词：命中其一才认为「值得调模型压缩」。
_PRECISION_KEYWORDS = re.compile(
    r"(path|command|cmd|id:|port|version|token|端口|版本|命令|路径|标识)",
    re.IGNORECASE,
)
# 精确值回填上限（避免补充段本身过大，§1.5 missing[:20]）。
_PRECISION_BACKFILL_MAX = 20


def extract_precision_values(text: str) -> list[str]:
    """按 PRECISION_PATTERNS 从原文提取精确值（去重、保序）。"""
    found: list[str] = []
    seen: set[str] = set()
    for pat in PRECISION_PATTERNS:
        for m in re.findall(pat, text or ""):
            if m not in seen:
                seen.add(m)
                found.append(m)
    return found


def contains_precision_keyword(text: str) -> bool:
    """门禁 C2：被压缩内容是否含精确值关键词（或直接含精确值模式命中）。"""
    if _PRECISION_KEYWORDS.search(text or ""):
        return True
    return bool(extract_precision_values(text or ""))


def should_use_llm_summary(
    older_messages: Sequence[Any],
    *,
    compress_count: int = 0,
    min_tokens: int = 5_000,
    max_llm_calls: int = 3,
) -> bool:
    """LLM 摘要门禁（§1.4）：三条件全为真才调模型，否则走规则截断。

    C1. 被压缩消息总 token >= `min_tokens`（太少不划算）
    C2. 被压缩内容含精确值关键词 / 精确值模式
    C3. 本会话 `compress_count < max_llm_calls`（防反复烧钱）
    """
    serialized = serialize_messages(older_messages)
    # C1：被压缩内容的体量。
    if estimate_tokens(serialized) < min_tokens:
        return False
    # C2：是否存在值得保全的精确值。
    if not contains_precision_keyword(serialized):
        return False
    # C3：本会话 LLM 摘要次数上限。
    if compress_count >= max_llm_calls:
        return False
    return True


def enforce_precision(summary: str, older_messages: Sequence[Any]) -> str:
    """压缩后精确值回填（§1.5，零 LLM 成本、确定性保证）。

    从被压缩原文提取精确值，凡摘要里缺失的追加到 `[精确值补充]` 段。
    """
    original = serialize_messages(older_messages)
    critical = extract_precision_values(original)
    missing = [t for t in critical if t and t not in (summary or "")]
    if not missing:
        return summary
    backfill = "\n".join(f"- {t}" for t in missing[:_PRECISION_BACKFILL_MAX])
    return f"{summary}\n\n[精确值补充]\n{backfill}"
