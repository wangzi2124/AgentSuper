# -*- coding: utf-8 -*-
"""P8 补丁：修复 token 估算低估 + 压缩触发过晚 + 收尾路径缺闭环。

依据（analyze_token_trace 实测报告）：
  A. 估算器低估 ~13%：round8 估算 20,851 vs 实际 23,599（+13.2%）；
     round9 实际 25,779 已超 usable 23,808 —— 截断基于低估估算"以为没超"而不触发。
  B. 压缩触发太晚：阈值 0.8×usable=19,046，round 8 才首次触发，压缩后下一轮仍超限。
  C. 强制收尾路径（max_tool_rounds 兜底，即 trace 中"无 round_start 的最后一轮"）
     此前仅有截断且同样受低估估算影响可能不触发。

修复：
  1. app/config.py           新增 token_estimate_correction=1.13、compaction_threshold_ratio=0.65
  2. app/context/token_counter.py  estimate_tokens 结果乘校正系数（全链路截断/压缩判断自动修正）
  3. app/context/budget.py   压缩阈值默认 0.8 → 0.65 × usable（提前 2-3 轮介入）
  4. app/agent/graph.py      强制收尾路径补齐 prune→压缩→截断 闭环（与主循环同款）

用法：
  python token_patch/fix_token_budget_p8.py            # 应用（自动备份 *.bak_p8）
  python token_patch/fix_token_budget_p8.py --verify   # 校验是否已应用
  python token_patch/fix_token_budget_p8.py --rollback # 回滚（从 .bak_p8 恢复）
"""

import argparse
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------- patches

PATCHES = [
    {
        "file": "app/config.py",
        "old": (
            "    # 输出预留：留给模型回答的 token（≈ min(20_000, maxOutputTokens)，默认 8_192）\n"
            "    context_reserve_tokens: int = 8_192\n"
            "    # 压缩触发阈值（token）；0 表示自动 = 0.8 × usable，长工具循环在截断兜底之前先压缩\n"
            "    compaction_threshold_tokens: int = 0\n"
        ),
        "new": (
            "    # 输出预留：留给模型回答的 token（≈ min(20_000, maxOutputTokens)，默认 8_192）\n"
            "    context_reserve_tokens: int = 8_192\n"
            "    # [token 优化 P8] cl100k_base 对 DeepSeek tokenizer 系统性低估（实测 +13.2%：\n"
            "    # round8 估算 20,851 vs 实际 23,599）。用于 token_counter 估算校正，避免\n"
            "    # 截断/压缩判断\"以为没超、实际已超\"（实测 round9 实际 25,779 超 usable 23,808）\n"
            "    token_estimate_correction: float = 1.13\n"
            "    # [token 优化 P8] 压缩触发比例：usable × ratio。原 0.8 实测 round 8 才触发、\n"
            "    # 压缩后下一轮仍超限；降到 0.65 提前 2-3 轮介入，压平长工具循环 token 曲线\n"
            "    compaction_threshold_ratio: float = 0.65\n"
            "    # 压缩触发阈值（token）；0 表示自动 = usable × compaction_threshold_ratio，长工具循环在截断兜底之前先压缩\n"
            "    compaction_threshold_tokens: int = 0\n"
        ),
        "count": 1,
    },
    {
        "file": "app/context/token_counter.py",
        "old": (
            "_encoder = None\n"
            "\n"
            "\n"
            "def _get_encoder():\n"
        ),
        "new": (
            "_encoder = None\n"
            "_correction = None\n"
            "\n"
            "\n"
            "def _estimate_correction() -> float:\n"
            "    \"\"\"cl100k_base → DeepSeek tokenizer 的估算校正系数。\n"
            "\n"
            "    [token 优化 P8] 实测 tiktoken cl100k_base 对 DeepSeek tokenizer 系统性低估\n"
            "    ~13%（analyze_token_trace：round8 估算 20,851 vs 实际 23,599）。在\n"
            "    estimate_tokens 一处校正，truncate_messages / compactor.should_compact 等\n"
            "    所有下游判断自动随之修正。惰性求值避免模块加载顺序问题。\n"
            "    \"\"\"\n"
            "    global _correction\n"
            "    if _correction is None:\n"
            "        try:\n"
            "            from app.config import settings\n"
            "            _correction = max(1.0, float(getattr(settings, \"token_estimate_correction\", 1.13)))\n"
            "        except Exception:\n"
            "            _correction = 1.13\n"
            "    return _correction\n"
            "\n"
            "\n"
            "def _get_encoder():\n"
        ),
        "count": 1,
    },
    {
        "file": "app/context/token_counter.py",
        "old": (
            "    enc = _get_encoder()\n"
            "    if enc and enc is not False:\n"
            "        try:\n"
            "            return len(enc.encode(text))\n"
            "        except Exception:\n"
            "            pass\n"
            "    # Fallback: ~4 chars per token (conservative for English)\n"
            "    return max(1, len(text) // 4)\n"
        ),
        "new": (
            "    enc = _get_encoder()\n"
            "    corr = _estimate_correction()\n"
            "    if enc and enc is not False:\n"
            "        try:\n"
            "            return max(1, round(len(enc.encode(text)) * corr))\n"
            "        except Exception:\n"
            "            pass\n"
            "    # Fallback: ~4 chars per token (conservative for English)\n"
            "    return max(1, round(len(text) / 4 * corr))\n"
        ),
        "count": 1,
    },
    {
        "file": "app/context/budget.py",
        "old": (
            "    if settings.compaction_threshold_tokens > 0:\n"
            "        return settings.compaction_threshold_tokens\n"
            "    return max(1, int(usable_context_tokens() * 0.8))\n"
        ),
        "new": (
            "    if settings.compaction_threshold_tokens > 0:\n"
            "        return settings.compaction_threshold_tokens\n"
            "    ratio = getattr(settings, \"compaction_threshold_ratio\", 0.65)\n"
            "    return max(1, int(usable_context_tokens() * ratio))\n"
        ),
        "count": 1,
    },
    {
        "file": "app/agent/graph.py",
        "old": (
            "                messages.append({\"role\": \"tool\", \"tool_call_id\": tc_id, \"tool_name\": tool_name, \"content\": bounded_result})\n"
            "            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))\n"
            "            # 对齐 opencode max-steps 语义：达到上限后工具禁用，仅注入收尾总结提示（assistant 角色）\n"
            "            messages.append({\"role\": \"assistant\", \"content\": MAX_STEPS_PROMPT})\n"
        ),
        "new": (
            "                messages.append({\"role\": \"tool\", \"tool_call_id\": tc_id, \"tool_name\": tool_name, \"content\": bounded_result})\n"
            "            # [token 优化 P8] 强制收尾路径补齐\"清理→压缩→截断\"闭环：此前仅截断，\n"
            "            # 且截断基于低估估算可能不触发（实测收尾轮裸发 25,779 超 usable 23,808）。\n"
            "            # 与主循环保持同款处理，避免收尾调用成为单请求内最大单次 pt。\n"
            "            trace_messages(\"graph.final_round_start\", messages)  # [token trace v8]\n"
            "            messages = prune_tool_outputs(\n"
            "                messages,\n"
            "                protect_tokens=settings.tool_output_protect_tokens,\n"
            "                minimum_tokens=settings.tool_output_prune_minimum_tokens,\n"
            "                tail_turns=settings.context_tail_turns,\n"
            "            )\n"
            "            if compactor.should_compact(messages):\n"
            "                self._push_event(state, {\"type\": \"step_start\", \"step_id\": \"compaction\", \"name\": \"压缩上下文\", \"status\": \"running\"})\n"
            "                old_count = len(messages)\n"
            "                messages = await compactor.compact(messages)\n"
            "                messages = sanitize_tool_messages(messages)\n"
            "                if state.get(\"_task\"):\n"
            "                    state[\"_task\"].record_compaction()\n"
            "                self._push_event(state, {\"type\": \"step_end\", \"step_id\": \"compaction\", \"name\": \"压缩上下文\", \"status\": \"completed\", \"detail\": f\"{old_count} 条消息压缩为 {len(messages)} 条\"})\n"
            "            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))\n"
            "            trace_messages(\"graph.final_round_ready\", messages)  # [token trace v8]\n"
            "            # 对齐 opencode max-steps 语义：达到上限后工具禁用，仅注入收尾总结提示（assistant 角色）\n"
            "            messages.append({\"role\": \"assistant\", \"content\": MAX_STEPS_PROMPT})\n"
        ),
        "count": 1,
    },
]

BAK_SUFFIX = ".bak_p8"


# ---------------------------------------------------------------- helpers

def _target(patch) -> Path:
    return BACKEND / patch["file"]


def _backup(patch) -> Path:
    return _target(patch).with_suffix(_target(patch).suffix + BAK_SUFFIX)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- actions

def apply() -> int:
    applied = 0
    for patch in PATCHES:
        target = _target(patch)
        backup = _backup(patch)
        if not target.exists():
            print(f"[skip] 文件不存在: {patch['file']}")
            continue
        text = _read(target)
        count = text.count(patch["old"])
        if count != patch["count"]:
            print(f"[FAIL] {patch['file']}: 匹配 {count} 处（期望 {patch['count']}），已跳过（可能已应用或文件已变化）")
            continue
        if not backup.exists():
            shutil.copy2(target, backup)
            print(f"[bak ] {patch['file']} -> {backup.name}")
        _write(target, text.replace(patch["old"], patch["new"], patch["count"]))
        print(f"[ok  ] {patch['file']} 已应用")
        applied += 1
    print(f"\n完成：{applied}/{len(PATCHES)} 处补丁已应用")
    if applied < len(PATCHES):
        print("提示：失败的补丁可用 --verify 复查；文件可能已部分应用，勿重复 --rollback 混用。")
    return 0 if applied == len(PATCHES) else 1


def verify() -> int:
    ok = True
    for patch in PATCHES:
        target = _target(patch)
        if not target.exists():
            print(f"[MISS] {patch['file']} 不存在")
            ok = False
            continue
        text = _read(target)
        old_left = text.count(patch["old"])
        new_present = patch["new"] in text
        state = "已应用" if (old_left == 0 and new_present) else ("未应用" if old_left == patch["count"] else "部分/异常")
        flag = "ok" if state == "已应用" else "!!"
        print(f"[{flag}] {patch['file']}: {state} (old 残留 {old_left}, new 存在 {new_present})")
        ok = ok and state == "已应用"
    return 0 if ok else 1


def rollback() -> int:
    restored = 0
    for patch in PATCHES:
        target = _target(patch)
        backup = _backup(patch)
        if backup.exists():
            shutil.copy2(backup, target)
            print(f"[ok  ] {patch['file']} 已从 {backup.name} 恢复")
            restored += 1
        else:
            print(f"[skip] {patch['file']} 无备份 {backup.name}，跳过")
    return 0 if restored == len(PATCHES) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="P8 token 预算修复补丁")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="应用补丁（默认）")
    group.add_argument("--verify", action="store_true", help="校验补丁状态")
    group.add_argument("--rollback", action="store_true", help="回滚补丁")
    args = parser.parse_args()

    if args.verify:
        return verify()
    if args.rollback:
        return rollback()
    return apply()


if __name__ == "__main__":
    sys.exit(main())
