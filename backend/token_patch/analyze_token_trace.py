#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析 logs/token_trace_*.jsonl，输出「每请求 / 每轮 token 画像」报告。

用法:
    python token_patch/analyze_token_trace.py                # 读 logs/token_trace_*.jsonl
    python token_patch/analyze_token_trace.py logs/token_trace_20260811.jsonl
    python token_patch/analyze_token_trace.py --top 10       # 展示消耗最大的 N 个请求
    python token_patch/analyze_token_trace.py --curve 3      # 打印某请求的逐轮曲线

输出:
    全局汇总 + 每请求累计（Top N）+ 单请求逐轮 token 曲线。
    核心结论行: 单请求累计 prompt = Σ(llm.usage.pt)，直接对照「40W/60W」观测。
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_events(paths):
    events = []
    for f in paths:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    events.sort(key=lambda e: e.get("ts", 0))
    return events


def group_by_batch(events):
    batches = defaultdict(list)
    for e in events:
        batches[e.get("batch", 0)].append(e)
    return batches


def summarize(events):
    usage = [e for e in events if e["event"] == "llm.usage"]
    finish = [e for e in events if e["event"] == "graph.finish"]
    entry = [e for e in events if e["event"] == "graph.entry_ready"]
    pre = [e for e in events if e["event"] == "graph.pre_compact"]
    pt = sum(int(e.get("pt", 0) or 0) for e in usage)
    ct = sum(int(e.get("ct", 0) or 0) for e in usage)
    comp_triggered = 0
    for e in pre:
        t = e.get("tokens", -1)
        th = e.get("threshold")
        if t >= 0 and th and t > th:
            comp_triggered += 1
    return {
        "llm_calls": len(usage),
        "pt_total": pt,
        "ct_total": ct,
        "pt_max": max((int(e.get("pt", 0) or 0) for e in usage), default=0),
        "batches": len(entry) or len(set(e.get("batch") for e in events)),
        "finish": finish,
        "pre_compact": len(pre),
        "comp_triggered": comp_triggered,
    }


def fmt(n):
    return f"{n:,}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="日志文件；缺省读 logs/token_trace_*.jsonl")
    ap.add_argument("--top", type=int, default=8, help="展示 token 消耗最大的 N 个请求")
    ap.add_argument("--curve", type=int, default=None, help="打印指定 batch 的逐轮曲线")
    args = ap.parse_args()

    files = args.paths or sorted(Path("logs").glob("token_trace_*.jsonl"))
    if not files:
        print("未找到日志：logs/token_trace_*.jsonl（先运行 add_token_trace_logging.py 并复现问题）")
        return 1
    print("日志文件: %s" % ", ".join(str(f) for f in files))

    events = load_events(files)
    if not events:
        print("日志为空")
        return 1
    batches = group_by_batch(events)

    # 全局汇总
    g = summarize(events)
    # 压缩阈值取日志中最后一次 pre_compact 携带的阈值（避免硬编码过时值）
    pre_all = [e for e in events if e["event"] == "graph.pre_compact"]
    _th = next((e.get("threshold") for e in reversed(pre_all) if e.get("threshold")), 19046)
    print("\n==== 全局汇总 ====")
    print(f"请求批次      : {g['batches']}")
    print(f"LLM 调用次数  : {g['llm_calls']}")
    print(f"prompt 累计   : {fmt(g['pt_total'])} tokens（均值 {fmt(g['pt_total'] // max(g['llm_calls'],1))}/次）")
    print(f"completion 累计: {fmt(g['ct_total'])} tokens")
    print(f"单次调用最大 pt: {fmt(g['pt_max'])}（上限 {fmt(23808)} usable）")
    print(f"压缩判断次数  : {g['pre_compact']}，其中触发压缩(>{fmt(_th)}): {g['comp_triggered']}")

    # 每请求画像
    rows = []
    for b, evs in batches.items():
        u = summarize(evs)
        rounds = len([e for e in evs if e["event"] in ("graph.round_start", "graph.final_round_start")])
        f = u["finish"][-1] if u["finish"] else {}
        dur = f.get("duration_ms")
        dur_s = round((dur or 0) / 1000, 1)
        rows.append({
            "batch": b,
            "llm_calls": u["llm_calls"],
            "pt_total": u["pt_total"],
            "pt_max": u["pt_max"],
            "ct_total": u["ct_total"],
            "rounds": max(rounds, int(f.get("rounds", 0) or 0)),
            "dur_s": dur_s,
        })
    rows.sort(key=lambda r: r["pt_total"], reverse=True)

    print("\n==== 每请求累计（按 prompt 降序，Top %d）====" % args.top)
    print(f"{'batch':>5} | {'rounds':>6} | {'llm_calls':>9} | {'pt_total':>12} | {'pt_max/次':>9} | {'ct_total':>9} | {'dur_s':>6}")
    for r in rows[: args.top]:
        print(f"{r['batch']:>5} | {r['rounds']:>6} | {r['llm_calls']:>9} | {fmt(r['pt_total']):>12} | {fmt(r['pt_max']):>9} | {fmt(r['ct_total']):>9} | {r['dur_s']:>6}")

    # 最大请求逐轮曲线
    target = args.curve if args.curve is not None else (rows[0]["batch"] if rows else None)
    if target is not None and target in batches:
        evs = batches[target]
        starts = {e["ts"]: e for e in evs if e["event"] in ("graph.round_start", "graph.final_round_start")}
        readys = [e for e in evs if e["event"] in ("graph.round_ready", "graph.final_round_ready")]
        usages = [e for e in evs if e["event"] == "llm.usage"]
        pre = [e for e in evs if e["event"] == "graph.pre_compact"]
        entry = [e for e in evs if e["event"] == "graph.entry_ready"]
        print("\n==== 请求 #%d 逐轮曲线（token 画像）====" % target)
        if entry:
            print(f"entry_ready  : tokens={fmt(entry[0].get('tokens', -1))} msgs={entry[0].get('msg_count')}  ← 首轮 LLM 前上下文")
        rounds_n = max(len(starts), len(readys), len(usages))
        for i in range(rounds_n):
            rs = starts[sorted(starts)[i]] if i < len(starts) else None
            rr = readys[i] if i < len(readys) else None
            up = usages[i] if i < len(usages) else None
            pc = pre[i] if i < len(pre) else None
            t_start = fmt(rs["tokens"]) if rs else "-"
            t_ready = fmt(rr["tokens"]) if rr else "-"
            t_pc = fmt(pc["tokens"]) if pc else "-"
            pt = fmt(up["pt"]) if up else "-"
            ct = fmt(up["ct"]) if up else "-"
            mark = " *触发压缩" if (pc and pc.get("threshold") and pc.get("tokens", -1) > pc.get("threshold", 0)) else ""
            print(f"round {i+1:>2} | prune前={t_start:>9} | 压缩前={t_pc:>9}{mark} | 发送前={t_ready:>9} | llm_pt={pt:>9} | llm_ct={ct:>6}")
        # 结论行
        total = sum(int(u.get("pt", 0) or 0) for u in usages)
        print(f"\n→ 请求 #{target} 累计 prompt = {fmt(total)} tokens"
              f"（≈ {len(usages)} 轮 × 均值 {fmt(total // max(len(usages), 1))}/轮）")
        if total >= 300_000:
            print("→ 与观测到的「40W」级暴增吻合：单请求 16 轮工具循环 × ~24K/轮 是主因。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
