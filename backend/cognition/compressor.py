"""压缩原语（阈值/tail/摘要编排在 ``harness/compression.py``）。

- 8 段式结构化摘要模板（prompts/nodes/compress.md，对齐 opencode）。
- 精确值提取与确定性回填；规则兜底摘要。
- 消息序列化：工具输出截断、媒体只留 ``[图片]``，不把 base64 当文本。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

from prompts.loader import PromptLoader

logger = logging.getLogger(__name__)

TOOL_OUTPUT_MAX_CHARS = 2000

_ROLE_LABELS = {
    "human": "User",
    "user": "User",
    "ai": "Assistant",
    "assistant": "Assistant",
    "system": "System",
    "tool": "Tool result",
}


def estimate_tokens(text: str) -> int:
    """Deprecated alias: keep one ruler (``cognition.token_counter``)."""
    from cognition.token_counter import estimate_tokens as _count

    return _count(text)


_MEDIA_BLOCK_TYPES = frozenset({"image_url", "image", "input_image"})


def _content_text(content: Any) -> str:
    """Extract text blocks; media becomes a marker, never a base64 payload."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if str(part.get("type") or "") in _MEDIA_BLOCK_TYPES:
                    parts.append("[图片]")
                elif isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        if str(content.get("type") or "") in _MEDIA_BLOCK_TYPES:
            return "[图片]"
        return str(content.get("text") or "")
    return str(content or "")


def _msg_text(m: Any) -> str:
    if isinstance(m, str):
        return m
    content = getattr(m, "content", None)
    if content is None and isinstance(m, dict):
        content = m.get("content", "")
    return _content_text(content)


def _msg_role(m: Any) -> str:
    role = getattr(m, "type", None)
    if role is None and isinstance(m, dict):
        role = m.get("role") or m.get("type")
    return _ROLE_LABELS.get(str(role), "User")


def serialize_messages(messages: Sequence[Any], *, strip_media: bool = True) -> str:
    """序列化为带角色标记的纯文本（工具结果截断），供摘要使用。

    ``strip_media`` 保留为显式语义：媒体块永远只序列化为 ``[图片]``，
    绝不把 base64 data URL 当文本喂给摘要模型。
    """
    lines: list[str] = []
    for m in messages:
        role = _msg_role(m)
        text = _msg_text(m)
        if role in ("Tool result", "tool"):
            text = text[:TOOL_OUTPUT_MAX_CHARS]
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls is None and isinstance(m, dict):
            tool_calls = m.get("tool_calls")
        if tool_calls:
            rendered: list[str] = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = str(fn.get("name") or tc.get("name") or "")
                    args = fn.get("arguments", tc.get("args"))
                else:
                    name = str(getattr(tc, "name", "") or "")
                    args = getattr(tc, "args", None)
                raw_args = args if isinstance(args, str) else (
                    json.dumps(args or {}, ensure_ascii=False, default=str) if args else ""
                )
                rendered.append(f"{name}({raw_args[:500]})" if raw_args else name)
            lines.append(f"[{role}/tool call] " + ", ".join(rendered))
        if text:
            lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def build_compress_prompt(existing_summary: str, serialized: str) -> str:
    """用 8 段式模板渲染压缩提示词。"""
    body = f"<previous-summary>\n{existing_summary or '（无）'}\n</previous-summary>\n\n{serialized}"
    return PromptLoader().load("nodes/compress.md", content=body)


# ── 精确值回填（agent-1.md §1.5）──
# 摘要生成后用纯规则确定性地回填精确值，不依赖模型「记得住」。

# 精确值模式（§1.5 PRECISION_PATTERNS）：文件路径 / 长数字（ID、端口）/ 反引号命令。
PRECISION_PATTERNS = [
    r"[/\\][\w./\\-]+\.\w{2,4}",   # 文件路径（含 Windows 反斜杠）
    r"\b\d{4,}\b",                 # 长数字（ID、端口、token 计数）
    r"`[^`]+`",                    # 反引号命令
]
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

_FALLBACK_SNIPPET_CHARS = 240
_FALLBACK_MAX_PRECISION = 20


def _snippet(text: str, limit: int = _FALLBACK_SNIPPET_CHARS) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] + ("…" if len(value) > limit else "")


def build_fallback_summary(
    messages: Sequence[Any], existing_summary: str = ""
) -> str:
    """确定性抽取式摘要：LLM 门禁不过/调用失败时兜底，避免“丢历史还无摘要”。"""
    msgs = list(messages or [])
    users = [m for m in msgs if _msg_role(m) in ("user", "human")]
    assistants = [m for m in msgs if _msg_role(m) in ("assistant", "ai")]

    goal = _snippet(_msg_text(users[0])) if users else "无"
    done = [f"- {_snippet(_msg_text(m))}" for m in assistants[-3:] if _msg_text(m).strip()]
    facts = extract_precision_values(serialize_messages(msgs))[:_FALLBACK_MAX_PRECISION]
    critical = [f"- {fact}" for fact in facts]
    files = [f"- {fact}" for fact in facts if "/" in fact or "\\" in fact][:10]
    next_step = _snippet(_msg_text(users[-1])) if users else "无"

    sections = [
        "## Goal",
        f"- {goal}",
        "## Constraints & Preferences",
        "- 无",
        "## Progress",
        "### Done",
        "\n".join(done) or "- 无",
        "### In Progress",
        f"- {_snippet(_msg_text(users[-1])) if len(users) > 1 else '无'}",
        "### Blocked",
        "- 无",
        "## Key Decisions",
        "- 无（规则兜底摘要无法可靠提取决策，请以 Critical Context 为准）",
        "## Next Steps",
        f"- {next_step}",
        "## Critical Context",
        "\n".join(critical) or "- 无",
        "## Relevant Files",
        "\n".join(files) or "- 无",
    ]
    fallback = "\n".join(sections)
    if existing_summary.strip():
        return f"{existing_summary.strip()}\n\n{fallback}"
    return fallback
