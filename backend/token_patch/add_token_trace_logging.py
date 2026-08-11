#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第七波补丁 v7：Token 全流程日志埋点（从「问助手」到 LLM 返回）。

背景:
  观测到 token 消耗出现 2W → 40W → 60W 的暴增。经审计（TOKEN_OPTIMIZATION_REPORT.md）:
  40W ≈ 单请求 16 轮工具循环 × ~24K/轮；60W ≈ 两次请求叠加。为在真实流量中
  精确定位「哪一轮、哪一环节」消耗最大，本波在 Agent 主循环与 LLM 记账入口
  埋结构化日志（JSON Lines），配合 analyze_token_trace.py 输出画像报告。

改动:
  新文件 app/trace_log.py : trace()/trace_messages() 单例（线程安全、失败静默）
  app/agent/graph.py      : 8 处插入（entry_ready / round_start / pre_compact /
                            round_ready / llm.usage×3 / graph.finish）
  app/monitor.py          : 1 处插入（record_model_call 总入口兜底）

用法:
    python token_patch/add_token_trace_logging.py            # 应用
    python token_patch/add_token_trace_logging.py --verify   # 校验
    python token_patch/add_token_trace_logging.py --rollback # 回滚

安全性:
  - 每条替换 count 校验：0=MISS（已应用或版本不符，不碰文件）、>1=SKIP（歧义，不碰文件）
  - 应用前自动备份 *.bak_token_trace
  - 新文件幂等：已存在则 SKIP
"""
import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_token_trace"

# ── 新文件（幂等：已存在则 SKIP）──
NEW_FILES: list[tuple[str, str]] = []

_TRACE_LOG_PY = '''# -*- coding: utf-8 -*-
"""Token 全流程追踪日志 [token 优化 v7]。

从「问助手」请求进入 Agent 到每次 LLM 返回，把关键节点以 JSON Lines 追加到
logs/token_trace_YYYYMMDD.jsonl，供 token_patch/analyze_token_trace.py 分析。

事件一览（event 字段）:
  graph.entry_ready       入口上下文就绪（system + 历史 + 检索注入后、首轮 LLM 前）
  graph.round_start       工具循环每轮开始时（prune 前）的上下文规模
  graph.pre_compact       本轮 prune 后、压缩判断前（threshold 附在 payload）
  graph.round_ready       本轮 truncate 后、发送 LLM 前的上下文规模
  llm.usage               每次 LLM 返回的实际 usage（pt/ct/dur/model/where）
  monitor.record_model_call  monitor.py 记账总入口（兜底，防漏）
  graph.finish            整个请求结束（rounds / tool_calls / 总耗时）

batch 字段：每次 entry_ready 视为新请求批次，自动 +1，便于按请求分组分析。
"""
import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_batch = {"n": 0}
_path: Path | None = None


def _log_path() -> Path:
    global _path
    if _path is None:
        base = os.environ.get("AGENTSUPER_LOG_DIR", "logs")
        d = Path(base)
        d.mkdir(parents=True, exist_ok=True)
        _path = d / f"token_trace_{time.strftime('%Y%m%d')}.jsonl"
    return _path


def trace(event: str, **payload) -> None:
    """写一条 JSON Line；任何失败静默忽略，绝不影响主流程。"""
    try:
        if event == "graph.entry_ready":
            _batch["n"] += 1
        rec = {"ts": round(time.time(), 3), "batch": _batch["n"], "event": event}
        rec.update(payload)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\\n")
    except Exception:
        pass


def trace_messages(event: str, messages: list, **payload) -> None:
    """估算消息列表 token 并写一条 trace（估算失败则只记条数）。"""
    try:
        from app.context.token_counter import estimate_tokens_messages
        payload["tokens"] = estimate_tokens_messages(messages)
    except Exception:
        payload["tokens"] = -1
    payload["msg_count"] = len(messages)
    trace(event, **payload)
'''

NEW_FILES.append(("app/trace_log.py", _TRACE_LOG_PY))

# ── 插入点补丁 ──
PATCHES: list[dict] = [
    # G1 import
    dict(
        file="app/agent/graph.py",
        old="from app.monitor import record_model_call",
        new="from app.monitor import record_model_call\nfrom app.trace_log import trace, trace_messages  # [token trace v7]",
        desc="G1: import trace/trace_messages",
    ),
    # G2 每轮开始（prune 前）
    dict(
        file="app/agent/graph.py",
        old="            messages = prune_tool_outputs(\n                messages,\n                protect_tokens=settings.tool_output_protect_tokens,",
        new="            trace_messages(\"graph.round_start\", messages)  # [token trace v7]\n            messages = prune_tool_outputs(\n                messages,\n                protect_tokens=settings.tool_output_protect_tokens,",
        desc="G2: 每轮开始记录上下文规模（prune 前）",
    ),
    # G3 每轮 prune 后、压缩判断前
    dict(
        file="app/agent/graph.py",
        old="            if compactor.should_compact(messages):",
        new="            trace_messages(\"graph.pre_compact\", messages, threshold=compaction_threshold_tokens())  # [token trace v7]\n            if compactor.should_compact(messages):",
        desc="G3: 每轮压缩判断前记录（含阈值，分析端对比是否触发）",
    ),
    # G4 入口截断后（首轮 LLM 前）
    # 注意：8 空格截断行是 12 空格行的子串（前 4 空格+正文），需带后续行消歧
    dict(
        file="app/agent/graph.py",
        old="        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))\n\n        response = await self._llm_call(model, messages, tool_defs, state=state)",
        new="        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))\n        trace_messages(\"graph.entry_ready\", messages)  # [token trace v7]\n\n        response = await self._llm_call(model, messages, tool_defs, state=state)",
        desc="G4: 入口上下文就绪（新请求批次起点）",
    ),
    # G5 每轮截断后（发送 LLM 前）——带 v5 重挂载注释行消歧（区别于 max-steps 强制收尾路径）
    dict(
        file="app/agent/graph.py",
        old="            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))\n            # [token 优化 v5] 每轮按需重挂载（核心常驻 + 意图命中 + 已使用保留），schema 固定开销大降",
        new="            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))\n            trace_messages(\"graph.round_ready\", messages)  # [token trace v7]\n            # [token 优化 v5] 每轮按需重挂载（核心常驻 + 意图命中 + 已使用保留），schema 固定开销大降",
        desc="G5: 每轮截断后、发送 LLM 前的上下文规模",
    ),
    # G6 _assemble_response 内 LLM 返回 usage
    dict(
        file="app/agent/graph.py",
        old="        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)",
        new="        trace(\"llm.usage\", where=\"assemble\", model=model, pt=pt, ct=ct, duration_ms=dur)  # [token trace v7]\n        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)",
        desc="G6: LLM 返回 usage（_assemble_response）",
    ),
    # G7 异常路径 usage
    dict(
        file="app/agent/graph.py",
        old="                record_model_call(model, duration_ms=dur)",
        new="                trace(\"llm.usage\", where=\"error\", model=model, pt=0, ct=0, duration_ms=dur)  # [token trace v7]\n                record_model_call(model, duration_ms=dur)",
        desc="G7: LLM 异常路径 usage",
    ),
    # G8 流式/invoke 路径 usage
    dict(
        file="app/agent/graph.py",
        old="        record_model_call(model, prompt_tokens=int(pt or 0), completion_tokens=int(ct or 0), duration_ms=dur)",
        new="        trace(\"llm.usage\", where=\"invoke\", model=model, pt=int(pt or 0), ct=int(ct or 0), duration_ms=dur)  # [token trace v7]\n        record_model_call(model, prompt_tokens=int(pt or 0), completion_tokens=int(ct or 0), duration_ms=dur)",
        desc="G8: LLM 返回 usage（invoke 路径）",
    ),
    # G9 请求结束汇总
    dict(
        file="app/agent/graph.py",
        old="        record_model_call(\n            model, duration_ms=(tmod.time() - _gen_start) * 1000,\n            tool_rounds=rounds, tool_calls=tool_calls_count,\n        )",
        new="        record_model_call(\n            model, duration_ms=(tmod.time() - _gen_start) * 1000,\n            tool_rounds=rounds, tool_calls=tool_calls_count,\n        )\n        trace(\"graph.finish\", rounds=rounds, tool_calls=tool_calls_count, duration_ms=(tmod.time() - _gen_start) * 1000)  # [token trace v7]",
        desc="G9: 请求结束汇总（轮数/工具调用/总耗时）",
    ),
    # M1 monitor 总入口兜底
    dict(
        file="app/monitor.py",
        old="    \"\"\"记录一次模型调用的统计信息（模型名、token 数、耗时、工具调用轮数与次数）。\"\"\"",
        new="    from app.trace_log import trace as _tr  # [token trace v7]\n    _tr(\"monitor.record_model_call\", model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, duration_ms=duration_ms, tool_rounds=tool_rounds, tool_calls=tool_calls)\n    \"\"\"记录一次模型调用的统计信息（模型名、token 数、耗时、工具调用轮数与次数）。\"\"\"",
        desc="M1: monitor 记账总入口兜底埋点",
    ),
]


# ── 工具函数 ──
def _apply_patch(p: dict, root: Path) -> tuple[str, str]:
    f = root / p["file"]
    text = f.read_text(encoding="utf-8")
    old, new = p["old"], p["new"]
    if new in text:
        return p["file"], "OK(already applied)"
    cnt = text.count(old)
    if cnt == 0:
        return p["file"], "MISS(anchor not found)"
    if cnt > 1:
        return p["file"], "SKIP(ambiguous anchor x%d)" % cnt
    f.write_text(text.replace(old, new, 1), encoding="utf-8")
    return p["file"], "APPLIED"


def _rollback_patch(p: dict, root: Path) -> tuple[str, str]:
    f = root / p["file"]
    text = f.read_text(encoding="utf-8")
    old, new = p["old"], p["new"]
    if new not in text:
        return p["file"], "SKIP(not applied)"
    f.write_text(text.replace(new, old, 1), encoding="utf-8")
    return p["file"], "ROLLED_BACK"


def main() -> int:
    ap = argparse.ArgumentParser(description="v7 Token 全流程日志埋点补丁")
    ap.add_argument("--verify", action="store_true", help="只校验，不修改")
    ap.add_argument("--rollback", action="store_true", help="回滚（从备份恢复 + 反向替换）")
    args = ap.parse_args()

    root = BACKEND_ROOT
    if args.rollback:
        # 反向替换
        for p in PATCHES:
            file, status = _rollback_patch(p, root)
            print("[ROLLBACK] %-28s %s" % (file, status))
        # 删除新文件
        for rel, _ in NEW_FILES:
            f = root / rel
            if f.exists():
                f.unlink()
                print("[ROLLBACK] 删除新文件 %s" % rel)
        print("完成。请重启后端服务。")
        return 0

    if args.verify:
        ok = True
        for p in PATCHES:
            f = root / p["file"]
            text = f.read_text(encoding="utf-8")
            if p["new"] in text:
                print("[VERIFY] %-28s OK(applied)" % p["file"])
            elif p["old"] not in text:
                print("[VERIFY] %-28s MISS(anchor not found)" % p["file"])
                ok = False
            else:
                print("[VERIFY] %-28s PENDING(not applied)" % p["file"])
                ok = False
        for rel, _ in NEW_FILES:
            print("[VERIFY] %-28s %s" % (rel, "OK(exists)" if (root / rel).exists() else "MISS(not created)"))
        print("VERIFY 结果: %s" % ("全部通过" if ok else "存在未应用项"))
        return 0 if ok else 1

    # 应用：先备份
    touched = set()
    for p in PATCHES:
        touched.add(root / p["file"])
    for f in touched:
        bak = Path(str(f) + BAK_SUFFIX)
        if not bak.exists():
            shutil.copy2(f, bak)
    # 新文件
    for rel, content in NEW_FILES:
        f = root / rel
        if f.exists():
            print("[NEW]     %-28s SKIP(exists)" % rel)
        else:
            f.write_text(content, encoding="utf-8")
            print("[NEW]     %-28s CREATED" % rel)
    # 插入点
    for p in PATCHES:
        file, status = _apply_patch(p, root)
        print("[PATCH]   %-28s %s" % (file, status))
    print("完成。请重启后端服务，然后复现长对话；日志写入 logs/token_trace_*.jsonl，")
    print("用 python token_patch/analyze_token_trace.py 分析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
