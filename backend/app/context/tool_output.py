"""Smart tool output bounding module.

Applies line-count and byte-size limits to tool outputs before they enter
the LLM context window. Prevents large outputs (file reads, shell commands)
from dominating the context.

Inspired by OpenCode's pruning strategy:
- Bounded output with clear truncation indicators
- Preserves error context (stderr) even when stdout is truncated
- Different limits for different output types
- Full output saved to disk when truncated (对齐 opencode `tool/truncate.ts`):
  the context only keeps a preview + a hint to continue reading via
  tool_read_file(offset) / tool_grep, so information is never permanently lost.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 截断文件保留时长（对齐 opencode truncate.ts RETENTION = 7 days）
_RETENTION_SECONDS = 7 * 24 * 3600


@dataclass
class ToolOutputLimits:
    """Configurable limits for tool output bounding."""
    max_lines: int = 200
    max_bytes: int = 32_768  # 32 KB
    # Tools that should have tighter limits (e.g., grep can produce massive output)
    tight_limits: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "tool_grep": (100, 16_384),
        "tool_execute": (300, 32_768),
        "tool_read_file": (200, 32_768),
    })


_DEFAULT_LIMITS = ToolOutputLimits()


def _truncation_dir() -> Path:
    """截断文件保存目录（默认 data/truncation，可用 AGENTSUPER_DATA 覆盖）。"""
    try:
        from app.storage.paths import global_paths
        return global_paths()["data"] / "truncation"
    except Exception:
        return Path(__file__).resolve().parents[2] / "data" / "truncation"


def _write_truncated(text: str) -> str:
    """把被截断的工具全文写入截断目录，返回磁盘绝对路径。

    文件名含时间戳与随机后缀，避免并发写冲突；目录不存在则创建。
    """
    directory = _truncation_dir()
    directory.mkdir(parents=True, exist_ok=True)
    suffix = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    path = directory / f"tool_{suffix}.txt"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to persist truncated tool output to %s: %s", path, e)
        return ""
    return str(path)


def cleanup_truncated(retention_seconds: int = _RETENTION_SECONDS) -> None:
    """清理超过保留期的截断文件（对齐 opencode truncate.ts cleanup，启动时调用）。"""
    try:
        directory = _truncation_dir()
        if not directory.exists():
            return
        cutoff = time.time() - retention_seconds
        for f in directory.glob("tool_*.txt"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def bound_tool_output(
    output: str,
    tool_name: str = "",
    limits: ToolOutputLimits | None = None,
    save_full_output: bool = True,
) -> str:
    """Apply smart bounding to tool output.

    Truncates output that exceeds line-count or byte-size limits.
    Preserves the beginning (most important) and appends a truncation notice.

    对齐 opencode `tool/truncate.ts`：超限时把完整输出写到 data/truncation/
    目录，上下文只保留预览 + 提示，模型可用 tool_read_file(offset) 或
    tool_grep 续读，避免截断导致信息永久丢失。

    Args:
        output: Raw tool output string.
        tool_name: Name of the tool (for applying tool-specific limits).
        limits: Custom limits (uses defaults if None).
        save_full_output: 超限时是否把全文写盘。默认 True（对齐 opencode）。

    Returns:
        Bounded output string, possibly truncated with a notice.
    """
    if not output:
        return output

    lim = limits or _DEFAULT_LIMITS
    max_lines, max_bytes = lim.max_lines, lim.max_bytes

    # Apply tool-specific tighter limits
    if tool_name in lim.tight_limits:
        max_lines, max_bytes = lim.tight_limits[tool_name]

    full_output = output
    original_bytes = len(output.encode("utf-8"))
    original_lines = output.count("\n") + 1

    truncated = False

    # Step 1: Truncate by line count
    if original_lines > max_lines:
        lines = output.split("\n")
        output = "\n".join(lines[:max_lines])
        truncated = True

    # Step 2: Truncate by byte size
    encoded = output.encode("utf-8")
    if len(encoded) > max_bytes:
        output = encoded[:max_bytes].decode("utf-8", errors="ignore")
        truncated = True

    if truncated:
        # 字节截断可能切在行中间 → 以最终输出重算实际显示行/字节数，保证 notice 精确
        truncated_lines = output.count("\n") + 1
        truncated_bytes = len(output.encode("utf-8"))
        saved = ""
        if save_full_output:
            saved = _write_truncated(full_output)
        hint = (
            f"\n\n[output truncated: showed {truncated_lines}/{original_lines} lines, "
            f"{truncated_bytes}/{original_bytes} bytes"
        )
        if saved:
            hint += f"; full output saved to {saved}]"
            hint += (
                "\nUse tool_read_file with offset to view specific sections, "
                "or tool_grep to search the full content."
            )
        else:
            hint += "]"
        output = output + hint
        logger.debug(
            "Tool output bounded: %s -> %d lines, %d bytes (from %d, %d)%s",
            tool_name, truncated_lines, truncated_bytes, original_lines, original_bytes,
            f"; saved to {saved}" if saved else "",
        )

    return output


def estimate_output_tokens(output: str) -> int:
    """Quick token estimate for a tool output string."""
    from app.context.token_counter import estimate_tokens
    return estimate_tokens(output)


_COMPACTION_MARKER = "[Task checkpoint"
_PRUNE_STUB_PREFIX = "[tool output pruned"


def prune_tool_outputs(
    messages: list[dict],
    protect_tokens: int = 40_000,
    minimum_tokens: int = 20_000,
    tail_turns: int = 2,
) -> list[dict]:
    """回溯式清理已进入上下文的旧工具输出，腾出 token 预算。

    对齐 opencode `compaction.ts:prune`：
    - 从最新消息往回走，越过最近 tail_turns 个**工具调用轮次**后才开始评估；
      （一个 role=assistant 且含 tool_calls 的消息计一个轮次 —— 本系统 prune
      在单次请求的工具循环内运行，请求通常只有一个 user turn，若按 user turn
      保护则所有工具输出都落在保护区内、永远无法回收，故对齐「轮次」粒度。）
    - 已完成工具输出累计 token 超过 protect_tokens 后，更旧的被替换为桩；
    - 仅当清理总量 >= minimum_tokens 才真正落桩，避免微小收益的频繁改写；
    - 遇到更早的压缩 checkpoint（system 角色 + 标记）即停止扫描。

    与入口 bounding（`bound_tool_output`）互补：前者限制新输出大小，
    本函数回收存量输出。返回新列表；未达到最低收益时原样返回。
    落桩前把原文写盘到 data/truncation/，桩文本带上保存路径提示，
    模型需要时可 tool_read_file 续读（对齐 opencode truncate.ts 语义）。
    """
    if not messages:
        return messages

    result = list(messages)

    # 提前构建 tool_call_id -> 工具名 映射：历史消息可能未带 tool_name 字段，
    # 从对应 assistant 消息的 tool_calls 反查，打桩时保留原始工具名便于排障。
    tool_name_by_id: dict[str, str] = {}
    for m in result:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if isinstance(fn, dict) and fn.get("name") and tc.get("id"):
                    tool_name_by_id[tc["id"]] = fn["name"]

    # 找出最后 tail_turns 个「工具调用轮次」所在的消息区间：
    # 从后往前，第 tail_turns 个 assistant(tool_calls) 消息之后的全部消息
    # 都属于「最近 N 轮」，予以保护（不参与回收）。
    last_n_assistant_idx: int | None = None
    rounds_seen = 0
    for i in range(len(result) - 1, -1, -1):
        m = result[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            rounds_seen += 1
            if rounds_seen == tail_turns:
                last_n_assistant_idx = i
                break
    # 无 tail_turns 个轮次时保护全部（避免误伤未成熟的工具循环）
    if last_n_assistant_idx is None:
        return messages

    total = 0
    pruned = 0
    candidates: list[int] = []

    for i in range(last_n_assistant_idx - 1, -1, -1):
        msg = result[i]
        role = msg.get("role")
        if role == "system" and str(msg.get("content", "")).startswith(_COMPACTION_MARKER):
            break
        if role != "tool":
            continue
        content = msg.get("content") or ""
        if content.startswith(_PRUNE_STUB_PREFIX):
            continue
        tokens = estimate_output_tokens(content)
        total += tokens
        if total <= protect_tokens:
            continue
        pruned += tokens
        candidates.append(i)

    if not candidates or pruned < minimum_tokens:
        return messages

    for i in candidates:
        content = result[i].get("content") or ""
        omitted = estimate_output_tokens(content)
        tool_name = (
            result[i].get("tool_name")
            or tool_name_by_id.get(result[i].get("tool_call_id"), "")
        )
        saved = _write_truncated(content)
        if tool_name:
            stub = f"[tool output pruned to save context space: {tool_name}, ~{omitted} tokens omitted]"
        else:
            stub = f"[tool output pruned to save context space: ~{omitted} tokens omitted]"
        if saved:
            stub += (
                f"\nFull output saved to: {saved}\n"
                "Use tool_read_file with offset to view specific sections, or tool_grep to search the full content."
            )
        result[i] = {**result[i], "content": stub}
    logger.info("Pruned %d tool outputs (~%d tokens) to free context budget", len(candidates), pruned)
    return result
