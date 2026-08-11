#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四波 token 优化补丁 v4：压缩优先于截断 + 多 Agent 汇总截断 + 子 Agent 裁剪口径修正。

改动清单（共 5 项，涉及 3 个文件）:
  D1. app/agent/graph.py : 首轮调用前 _truncate_messages 硬截断 → 先 compactor.should_compact
                          触发 LLM 压缩（保留事实摘要），截断仅作最后兜底。
                          背景：工具循环内每轮已有 压缩→截断 闭环，唯一缺失的是首轮
                          （多轮对话 history 大时直接丢弃旧消息 → 模型失忆重做，重做比压缩更贵）。
  D2. app/agent/supervisor.py : _synthesize 多 Agent 汇总时，子 Agent 结果超长截断
                          （SUB_RESULT_TRUNC=3000 字符）。完整答案已由单 Agent 路由直通用户，
                          汇总输入只需要点，避免 3 个子 Agent × 全文 拼进 synthesize 请求。
  D3. app/agent/sub_tools.py : _trim_messages 裁剪口径从"字符数"改为"真实 token 估算"。
                          原口径 12K 字符 ≈ 中文仅 3K token，过早裁剪导致子 Agent 失忆重做；
                          改为 estimate_tokens 后 12K token ≈ 4~5 万字符，裁剪时机更合理。

预估收益:
  - 多轮对话 + 长 history 场景：首轮不再丢历史，压缩后每轮省 O(history) 的重发
  - 多 Agent 分解场景：synthesize 输入从 Σ(子答案全文) 降至 ≤ 3×3K 字符
  - 子 Agent 工具循环：裁剪时机修正后重做率下降

安全性:
  - 每个替换前先 count 校验：0 次=已应用或版本不符（MISS 报告，不碰文件）；
    >1 次=歧义（SKIP 报告，不碰文件）
  - 应用前自动备份为 *.bak_token_patch4，可随时 --rollback 恢复

用法:
    python token_patch/apply_token_patch4.py            # 应用
    python token_patch/apply_token_patch4.py --verify   # 校验是否已应用
    python token_patch/apply_token_patch4.py --rollback # 回滚到备份
"""
import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_token_patch4"

PATCHES = [
    # ── D1. graph.py: 首轮调用前 压缩优先于硬截断 ──
    (
        "app/agent/graph.py",
        '''        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))

        response = await self._llm_call(model, messages, tool_defs, state=state)''',
        '''        # [token 优化 v4] 压缩优先于硬截断：首轮若已超压缩阈值，先 LLM 压缩（保留事实摘要），
        # 避免直接丢弃旧历史导致模型失忆重做（重做比压缩更贵）。截断仅作最后兜底。
        # 工具循环内每轮已有同款 压缩→截断 闭环，此处在入口补齐，覆盖多轮对话 history 场景。
        if compactor.should_compact(messages):
            self._push_event(state, {"type": "step_start", "step_id": "compaction", "name": "压缩上下文", "status": "running"})
            old_count = len(messages)
            messages = await compactor.compact(messages)
            messages = sanitize_tool_messages(messages)
            if state.get("_task"):
                state["_task"].record_compaction()
            self._push_event(state, {"type": "step_end", "step_id": "compaction", "name": "压缩上下文", "status": "completed", "detail": f"{old_count} 条消息压缩为 {len(messages)} 条"})
        messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))

        response = await self._llm_call(model, messages, tool_defs, state=state)''',
        "D1 首轮调用前 压缩优先于硬截断",
    ),
    # ── D2a. supervisor.py: 子 Agent 结果汇总截断常量 ──
    (
        "app/agent/supervisor.py",
        '''# ── 分解提示词 ──
DECOMPOSE_SYSTEM_PROMPT = """你是一个任务分解专家。将用户的复杂问题拆解成多个可以并行执行的子任务。''',
        '''# ── [token 优化 v4] 多 Agent 汇总截断：子 Agent 完整答案已直通用户，汇总只需要点 ──
SUB_RESULT_TRUNC = 3000  # 字符

# ── 分解提示词 ──
DECOMPOSE_SYSTEM_PROMPT = """你是一个任务分解专家。将用户的复杂问题拆解成多个可以并行执行的子任务。''',
        "D2a 汇总截断常量 SUB_RESULT_TRUNC",
    ),
    # ── D2b. supervisor.py: _synthesize 超长结果截断 ──
    (
        "app/agent/supervisor.py",
        '''        segments = []
        for i, r in enumerate(results):
            agent_label = {"rag": "知识库", "web_search": "网络搜索", "code": "代码分析"}.get(r["agent"], r["agent"])
            segments.append(
                f"[{agent_label} — {r.get('original_question', '')[:50]}]\\n{r['answer']}"
            )''',
        '''        segments = []
        for i, r in enumerate(results):
            agent_label = {"rag": "知识库", "web_search": "网络搜索", "code": "代码分析"}.get(r["agent"], r["agent"])
            # [token 优化 v4] 子 Agent 结果超长时截断，避免多结果汇总输入膨胀
            # （完整答案已由单 Agent 路由直接返回给用户；汇总仅需其要点）
            answer = r.get("answer", "")
            if len(answer) > SUB_RESULT_TRUNC:
                answer = answer[:SUB_RESULT_TRUNC] + f"\\n…[子 Agent 结果过长，已截断前 {SUB_RESULT_TRUNC} 字符]"
            segments.append(
                f"[{agent_label} — {r.get('original_question', '')[:50]}]\\n{answer}"
            )''',
        "D2b synthesize 汇总输入截断",
    ),
    # ── D3a. sub_tools.py: 引入 token 估算 ──
    (
        "app/agent/sub_tools.py",
        '''from app.agent.stream_events import emit, step_event
from app.config import settings''',
        '''from app.agent.stream_events import emit, step_event
from app.config import settings
from app.context.token_counter import estimate_tokens''',
        "D3a 引入 estimate_tokens",
    ),
    # ── D3b. sub_tools.py: _trim_messages 裁剪口径 字符数 → token ──
    (
        "app/agent/sub_tools.py",
        '''def _trim_messages(messages: list[dict]) -> list[dict]:
    """按估算 token 裁剪 messages；按“轮”丢弃最旧内容，保持 tool_call 配对完整。"""
    def _size(ms: list[dict]) -> int:
        return sum(
            len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or ""))
            for m in ms
        )''',
        '''def _trim_messages(messages: list[dict]) -> list[dict]:
    """按估算 token 裁剪 messages；按“轮”丢弃最旧内容，保持 tool_call 配对完整。"""
    # [token 优化 v4] 用真实 token 估算替代字符数：12K token ≈ 4~5 万字符，
    # 原字符数口径在中文场景把上限压到 ~3K token，过早裁剪导致子 Agent 失忆重做。
    def _size(ms: list[dict]) -> int:
        return sum(
            estimate_tokens(str(m.get("content") or "")) + estimate_tokens(str(m.get("tool_calls") or ""))
            for m in ms
        )''',
        "D3b 裁剪口径 字符数 -> token",
    ),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + BAK_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  [BACKUP] {path.name} -> {bak.name}")


def apply() -> int:
    ok = 0
    miss = 0
    skip = 0
    print(f"== 应用第四波补丁（backend 根: {BACKEND_ROOT}）==")
    for rel, old, new, label in PATCHES:
        path = BACKEND_ROOT / rel
        if not path.exists():
            print(f"  [MISS] {rel} 不存在，跳过: {label}")
            miss += 1
            continue
        content = _read(path)
        n = content.count(old)
        if n == 0:
            print(f"  [MISS] {rel} 未命中(可能已应用或版本不同): {label}")
            miss += 1
            continue
        if n > 1:
            print(f"  [SKIP] {rel} 出现 {n} 次，歧义跳过: {label}")
            skip += 1
            continue
        _backup(path)
        _write(path, content.replace(old, new, 1))
        print(f"  [ OK ] {rel} : {label}")
        ok += 1
    print(f"== 完成: 应用 {ok} 项, 未命中 {miss} 项, 跳过 {skip} 项 ==")
    if miss or skip:
        print("  有未命中/跳过项：请把输出发给我更新补丁；未命中通常=已应用过或源码版本不同。")
    return 0 if (miss == 0 and skip == 0) else 1


def verify() -> int:
    print(f"== 校验第四波补丁应用状态（backend 根: {BACKEND_ROOT}）==")
    all_ok = True
    for rel, old, new, label in PATCHES:
        path = BACKEND_ROOT / rel
        if not path.exists():
            print(f"  [MISS] {rel} 不存在: {label}")
            all_ok = False
            continue
        content = _read(path)
        applied = old not in content
        bak = path.with_name(path.name + BAK_SUFFIX)
        status = "已应用" if applied else "未应用"
        bak_status = f", 备份存在" if bak.exists() else ""
        print(f"  [{status}]{bak_status} {rel}: {label}")
        all_ok = all_ok and applied
    print("== 校验完成: " + ("全部已应用 ✓" if all_ok else "存在未应用项 ✗") + " ==")
    return 0 if all_ok else 1


def rollback() -> int:
    print(f"== 回滚第四波补丁（backend 根: {BACKEND_ROOT}）==")
    ok = 0
    miss = 0
    for rel, old, new, label in PATCHES:
        path = BACKEND_ROOT / rel
        if not path.exists():
            print(f"  [MISS] {rel} 不存在: {label}")
            miss += 1
            continue
        bak = path.with_name(path.name + BAK_SUFFIX)
        if not bak.exists():
            print(f"  [MISS] {rel} 无备份，跳过: {label}")
            miss += 1
            continue
        shutil.copy2(bak, path)
        print(f"  [ OK ] {rel} : 已从 {bak.name} 恢复")
        ok += 1
    print(f"== 完成: 恢复 {ok} 项, 缺失 {miss} 项 ==")
    return 0 if miss == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="第四波 token 优化补丁")
    parser.add_argument("--verify", action="store_true", help="校验是否已应用")
    parser.add_argument("--rollback", action="store_true", help="回滚到备份")
    args = parser.parse_args()
    if args.verify:
        return verify()
    if args.rollback:
        return rollback()
    return apply()


if __name__ == "__main__":
    sys.exit(main())
