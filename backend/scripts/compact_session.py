"""会话压缩器（对齐 opencode SessionCompaction 设计，自包含单文件）。

为什么有这个脚本
----------------
opencode 每轮请求 token 少的核心机制之一是「超限即压缩」：旧对话折叠成
锚定摘要（SUMMARY_TEMPLATE），只保留最近 tail_turns 轮原文。本项目
data/session.db 的 schema 与 app/session/history.py 已按 opencode 预留了
压缩水位（sessions.time_compacted、type='compaction' 消息、context epoch），
本脚本补上「执行压缩」这一环：

  1. 读取 session.db 全量消息 + parts，定位上次压缩水位 → 得到 head（旧对话）
  2. 划分轮次：以 user 消息为起点，从后往前保留最近 tail_turns 轮为 tail
  3. 把 head 序列化为模型可读文本（工具输出截断 2000 字符）
  4. 调用 LLM（litellm，配置优先 summarization_* → llm_*）按 opencode
     SUMMARY_TEMPLATE 生成/更新摘要
  5. 写回 compaction 消息（text part 存摘要）→ history.load() 之后会把
     摘要注入模型上下文最前；更新 time_compacted + 重建 epoch 水位

用法
----
  # 只估算收益（不调 LLM、不写库）：
  python scripts/compact_session.py --session <session_id> --dry-run

  # 用最近更新的会话，按溢出阈值自动判断（够大才压）：
  python scripts/compact_session.py

  # 强制执行压缩：
  python scripts/compact_session.py --session <session_id> --force

  # 列出所有会话（找 id 用）：
  python scripts/compact_session.py --list
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 项目根加入 sys.path，便于复用 app.config 的配置（失败则回退环境变量）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "session.db"

# ── 对齐 opencode 常量 ──────────────────────────────────────────────────────
TOOL_OUTPUT_MAX_CHARS = 2_000        # compaction.ts：历史中工具输出截断长度
SUMMARY_OUTPUT_TOKENS = 4_096        # compaction.ts：摘要输出上限
MIN_PRESERVE_RECENT_TOKENS = 2_000   # compaction.ts：尾部至少保留
MAX_PRESERVE_RECENT_TOKENS = 8_000   # compaction.ts：尾部最多保留
DEFAULT_TAIL_TURNS = 2               # compaction.ts：默认保留最近轮数
MIN_SAVINGS_TOKENS = 0

# opencode SUMMARY_TEMPLATE 原样
SUMMARY_TEMPLATE = """Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted."""


# ── 数据库访问 ───────────────────────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def list_sessions() -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, title, agent, model, status, tokens_input, tokens_output, "
            "       time_compacted, time_updated "
            "FROM sessions ORDER BY time_updated DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_messages(session_id: str) -> list[dict[str, Any]]:
    """全量消息（事件日志，seq 升序）。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT seq, id, type, data FROM session_messages "
            "WHERE session_id = ? ORDER BY seq", (session_id,)
        ).fetchall()
        return [{"seq": r["seq"], "id": r["id"], "type": r["type"],
                 "data": json.loads(r["data"]) if r["data"] else {}} for r in rows]
    finally:
        conn.close()


def load_parts(session_id: str) -> dict[str, list[dict[str, Any]]]:
    """message_id → parts 列表。"""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT message_id, type, data FROM message_parts WHERE session_id = ? "
            "ORDER BY time_created, id", (session_id,)
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            result.setdefault(r["message_id"], []).append(
                {"type": r["type"], "data": json.loads(r["data"]) if r["data"] else {}}
            )
        return result
    finally:
        conn.close()


def latest_compaction_seq(session_id: str) -> Optional[int]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT MAX(seq) AS n FROM session_messages "
            "WHERE session_id = ? AND type = 'compaction'", (session_id,)
        ).fetchone()
        return int(row["n"]) if row and row["n"] is not None else None
    finally:
        conn.close()


def write_compaction(session_id: str, *, mode: str, tail_start_id: str, tail_start_seq: int,
                     head_start_seq: int, summary: str) -> int:
    """写入 compaction 消息（text part 存摘要）+ 更新会话水位。"""
    conn = _connect()
    try:
        now = int(time.time() * 1000)
        conn.execute("BEGIN IMMEDIATE")
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM session_messages WHERE session_id = ?",
            (session_id,)
        ).fetchone()["n"]
        mid = f"msg_{seq}_{int(now)}"
        conn.execute(
            "INSERT INTO session_messages (seq, id, session_id, type, data, time_created) "
            "VALUES (?,?,?,?,?,?)",
            (seq, mid, session_id, "compaction",
             json.dumps({"mode": mode, "tail_start_id": tail_start_id,
                         "tail_start_seq": tail_start_seq,
                         "head_start_seq": head_start_seq}, ensure_ascii=False),
             now),
        )
        # text part：history.load() 把 compaction 消息插到上下文最前并提取该 part
        conn.execute(
            "INSERT INTO message_parts (id, session_id, message_id, type, data, time_created) "
            "VALUES (?,?,?,?,?,?)",
            (f"prt_{seq}_{now}_a", session_id, mid, "text",
             json.dumps({"text": summary}, ensure_ascii=False), now),
        )
        conn.execute(
            "INSERT INTO message_parts (id, session_id, message_id, type, data, time_created) "
            "VALUES (?,?,?,?,?,?)",
            (f"prt_{seq}_{now}_b", session_id, mid, "compaction",
             json.dumps({"mode": mode, "tail_start_id": tail_start_id,
                         "tail_start_seq": tail_start_seq}, ensure_ascii=False), now),
        )
        # 会话水位 + context epoch 重建（baseline_seq = 本 compaction 的 seq，
        # 与 history.load 的 `seq > after_seq` 语义配合，head 即被跳过；
        # snapshot 携带 tail_start_seq/id，使 load() 把保留的 tail 原文也回放）
        conn.execute(
            "UPDATE sessions SET time_compacted = ?, time_updated = ? WHERE id = ?",
            (now, now, session_id),
        )
        conn.execute(
            "INSERT INTO session_context_epoch (session_id, baseline, baseline_seq, snapshot, time_created, time_updated) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET baseline=excluded.baseline, "
            "baseline_seq=excluded.baseline_seq, snapshot=excluded.snapshot, time_updated=excluded.time_updated",
            (session_id, f"compaction:{seq}", seq,
             json.dumps({"tail_start_id": tail_start_id,
                         "tail_start_seq": tail_start_seq}, ensure_ascii=False), now, now),
        )
        conn.commit()
        return seq
    finally:
        conn.close()


# ── Token 估算 ──────────────────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    """轻量估算：对齐 opencode chars/4，但为中文加权（CJK ≈1.5 token/字）。

    纯英文与 opencode 口径一致；中文偏保守（宁高勿低，避免溢出后才发现超限）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
    return max(1, round(cjk * 1.5 + (len(text) - cjk) / 4))


def _truncate(value: str, limit: int = TOOL_OUTPUT_MAX_CHARS) -> str:
    return value if len(value) <= limit else f"{value[:limit]}\n[truncated]"


# ── 序列化（对齐 opencode compaction.ts serialize）──────────────────────────
def serialize_message(msg: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    mtype, data = msg["type"], msg["data"]
    if mtype == "user":
        text = data.get("text", "")
        files = data.get("files") or []
        refs = "".join(
            f"\n[Attached {f.get('mime', 'file')}: {f.get('name') or f.get('uri')}]"
            for f in files if isinstance(f, dict)
        )
        return f"[User]: {text}{refs}"
    if mtype == "assistant":
        chunks: list[str] = []
        for p in parts or []:
            ptype, pdata = p["type"], p.get("data", {})
            if ptype == "text" and pdata.get("text"):
                chunks.append(f"[Assistant]: {pdata['text']}")
            elif ptype == "reasoning" and (pdata.get("reasoning") or pdata.get("text")):
                chunks.append(f"[Assistant reasoning]: {pdata.get('reasoning') or pdata.get('text')}")
            elif ptype == "tool":
                args = pdata.get("args") or {}
                call = f"[Assistant tool call]: {pdata.get('name', '')}({json.dumps(args, ensure_ascii=False)})"
                if pdata.get("state") == "completed":
                    chunks.append(f"{call}\n[Tool result]: {_truncate(pdata.get('output') or '')}")
                elif pdata.get("state") == "error":
                    chunks.append(f"{call}\n[Tool error]: {pdata.get('error', 'unknown')}")
                else:
                    chunks.append(call)
        return "\n".join(chunks)
    if mtype == "system":
        return f"[System update]: {data.get('text', '')}"
    return ""


def serialize_history(messages: list[dict[str, Any]], parts: dict[str, list[dict[str, Any]]]) -> str:
    lines = []
    for m in messages:
        if m["type"] in ("compaction", "epoch"):
            continue
        s = serialize_message(m, parts.get(m["id"], []))
        if s:
            lines.append(s)
    return "\n\n".join(lines)


# ── 轮次与尾部选择（对齐 opencode turns / select / preserveRecentBudget）─────
def turns(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(start, end) 下标区间列表；user 消息为轮次起点。"""
    starts = [i for i, m in enumerate(messages) if m["type"] == "user"]
    return [(s, starts[k + 1] if k + 1 < len(starts) else len(messages))
            for k, s in enumerate(starts)]


def usable_tokens() -> int:
    try:
        from app.config import settings
        return max(0, settings.max_context_tokens - settings.context_reserve_tokens)
    except Exception:
        return 64_000 - 8_192


def compaction_threshold() -> int:
    try:
        from app.config import settings
        if settings.compaction_threshold_tokens > 0:
            return settings.compaction_threshold_tokens
    except Exception:
        pass
    return int(usable_tokens() * 0.8)


def _preserve_budget() -> int:
    try:
        from app.config import settings
        if settings.context_preserve_recent_tokens > 0:
            return settings.context_preserve_recent_tokens
    except Exception:
        pass
    return max(MIN_PRESERVE_RECENT_TOKENS,
               min(MAX_PRESERVE_RECENT_TOKENS, int(usable_tokens() * 0.25)))


def select_tail(messages: list[dict[str, Any]], parts: dict[str, list[dict[str, Any]]],
                tail_turns: int, budget: int) -> tuple[int, int]:
    """返回 (tail_start_idx, tail_end_idx)；预算内从后往前保留最近 tail_turns 轮。"""
    all_turns = turns(messages)
    if not all_turns:
        return len(messages), len(messages)
    recent = all_turns[-tail_turns:]
    total, kept = 0, []
    for t in reversed(recent):
        size = estimate_tokens(serialize_history(messages[t[0]:t[1]], parts))
        if kept and total + size > budget:
            break
        kept.append(t)
        total += size
    kept.reverse()
    if not kept:
        kept = [recent[-1]]            # 保底：至少保留最近一轮
    return kept[0][0], kept[-1][1]


# ── 摘要生成 ────────────────────────────────────────────────────────────────
def summary_config() -> tuple[str, Optional[str], Optional[str]]:
    try:
        from app.config import settings
        return (settings.summarization_model or settings.llm_model,
                settings.summarization_api_key or settings.llm_api_key,
                settings.summarization_api_base or settings.llm_api_base)
    except Exception:
        return ("deepseek-chat", None, "https://api.deepseek.com")


def build_summary_prompt(previous_summary: Optional[str], head_text: str) -> str:
    header = (
        "Update the anchored summary below using the conversation history above.\n"
        "Preserve still-true details, remove stale details, and merge in the new facts.\n"
        f"<previous-summary>\n{previous_summary}\n</previous-summary>"
        if previous_summary
        else "Create a new anchored summary from the conversation history."
    )
    return f"{header}\n\n{SUMMARY_TEMPLATE}\n\n{head_text}"


def generate_summary(prompt: str) -> str:
    import litellm
    model, api_key, api_base = summary_config()
    resp = litellm.completion(
        model=model, messages=[{"role": "user", "content": prompt}],
        api_key=api_key, api_base=api_base, max_tokens=SUMMARY_OUTPUT_TOKENS,
    )
    return (resp.choices[0].message.content or "").strip()


def previous_summary(session_id: str, messages: list[dict[str, Any]],
                     parts: dict[str, list[dict[str, Any]]],
                     prev_seq: Optional[int]) -> Optional[str]:
    if prev_seq is None:
        return None
    for m in messages:
        if m["type"] == "compaction" and m["seq"] == prev_seq:
            for p in parts.get(m["id"], []):
                if p["type"] == "text" and p.get("data", {}).get("text"):
                    return p["data"]["text"]
    return None


# ── 主流程 ──────────────────────────────────────────────────────────────────
@dataclass
class CompactResult:
    compacted: bool = False
    summary: str = ""
    head_start_seq: int = 0
    tail_start_id: str = ""
    tail_start_seq: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    savings: int = 0
    compaction_seq: int = 0
    skipped_reason: str = ""


def compact(session_id: str, *, force: bool = False, tail_turns: Optional[int] = None,
            dry_run: bool = False, min_savings: int = MIN_SAVINGS_TOKENS) -> CompactResult:
    """执行一次压缩；dry_run=True 时只估算不调 LLM 不写库。"""
    try:
        from app.config import settings
        tail_turns = tail_turns if tail_turns is not None else settings.context_tail_turns
    except Exception:
        tail_turns = tail_turns if tail_turns is not None else DEFAULT_TAIL_TURNS
    if tail_turns <= 0:
        return CompactResult(skipped_reason="tail_turns <= 0")

    messages = load_messages(session_id)
    parts = load_parts(session_id)
    prev_seq = latest_compaction_seq(session_id) or 0
    candidate = [m for m in messages if m["seq"] > prev_seq and m["type"] != "compaction"]
    if not candidate:
        return CompactResult(skipped_reason="no new messages since last compaction")

    budget = _preserve_budget()
    tail_start, tail_end = select_tail(messages, parts, tail_turns, budget)
    tail_msgs = messages[tail_start:tail_end]
    if not tail_msgs:
        return CompactResult(skipped_reason="no user turns to preserve")
    head_msgs = [m for m in candidate if m["seq"] < tail_msgs[0]["seq"]]
    head_text = serialize_history(head_msgs, parts)
    if not head_text.strip():
        return CompactResult(skipped_reason="head empty after tail selection")

    tail_text = serialize_history(tail_msgs, parts)
    prev_summary = previous_summary(session_id, messages, parts, prev_seq)
    before = estimate_tokens(head_text) + estimate_tokens(tail_text)
    after = estimate_tokens((prev_summary or "") + tail_text)
    savings = max(0, before - after)
    tail_start_id = tail_msgs[0]["id"]
    if savings < min_savings:
        return CompactResult(before_tokens=before, after_tokens=after, savings=savings,
                             tail_start_id=tail_start_id,
                             skipped_reason=f"savings {savings} < min_savings {min_savings}")

    summary = ""
    if not dry_run:
        summary = generate_summary(build_summary_prompt(prev_summary, head_text))

    result = CompactResult(
        compacted=not dry_run, summary=summary,
        head_start_seq=head_msgs[0]["seq"], tail_start_id=tail_start_id,
        tail_start_seq=tail_msgs[0]["seq"],
        before_tokens=before, after_tokens=after, savings=savings,
    )
    if not dry_run:
        result.compaction_seq = write_compaction(
            session_id, mode="auto", tail_start_id=tail_start_id,
            tail_start_seq=tail_msgs[0]["seq"],
            head_start_seq=result.head_start_seq, summary=summary,
        )
    return result


def maybe_compact(session_id: str, *, force: bool = False, **kwargs: Any) -> CompactResult:
    """溢出即压缩：估算当前上下文，达到 threshold 或 force=True 时执行。"""
    if force:
        return compact(session_id, force=True, **kwargs)
    estimate = estimate_tokens(serialize_history(load_messages(session_id), load_parts(session_id)))
    if estimate >= compaction_threshold():
        return compact(session_id, **kwargs)
    return CompactResult(skipped_reason=f"context {estimate} < threshold {compaction_threshold()}")


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="会话压缩器（对齐 opencode compaction）")
    parser.add_argument("--session", help="会话 id；省略时用最近更新的会话")
    parser.add_argument("--list", action="store_true", help="列出最近会话")
    parser.add_argument("--force", action="store_true", help="忽略溢出阈值强制执行")
    parser.add_argument("--dry-run", action="store_true", help="只估算收益，不调 LLM 不写库")
    parser.add_argument("--tail-turns", type=int, default=None, help="尾部保留轮数（默认取配置）")
    parser.add_argument("--verbose", action="store_true", help="打印完整摘要")
    args = parser.parse_args()

    if args.list:
        for s in list_sessions():
            compacted = "yes" if s["time_compacted"] else "no"
            print(f"{s['id']}  [{s.get('status','')}] compacted={compacted}  updated={s['time_updated']}  {s['title'][:40]}")
        return

    session_id = args.session
    if not session_id:
        sessions = list_sessions()
        if not sessions:
            print("未找到会话（--session 或 data/session.db 中无会话）")
            raise SystemExit(1)
        session_id = sessions[0]["id"]
        print(f"（未指定 --session，使用最近更新的会话）\n")

    if args.dry_run:
        # dry-run 始终估算收益（不经阈值门控，否则低于阈值时看不到 before/after）
        result = compact(session_id, tail_turns=args.tail_turns, dry_run=True)
    else:
        result = maybe_compact(session_id, force=args.force, tail_turns=args.tail_turns)

    print(f"session      : {session_id}")
    print(f"threshold    : {compaction_threshold()} tokens (usable={usable_tokens()})")
    print(f"before       : {result.before_tokens} tokens")
    print(f"after        : {result.after_tokens} tokens")
    print(f"savings      : {result.savings} tokens "
          f"({100 * result.savings / max(1, result.before_tokens):.1f}%)")
    print(f"tail_start   : {result.tail_start_id}")
    if result.compacted:
        print(f"compaction   : OK (seq={result.compaction_seq}, mode=auto)")
    else:
        print(f"compaction   : skipped ({result.skipped_reason})")
    if result.summary and (args.verbose or not result.compacted):
        print("\n--- summary ---\n" + result.summary)


if __name__ == "__main__":
    main()
