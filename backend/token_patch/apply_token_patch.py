#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token 优化补丁 — 应用 / 校验 / 回滚
=====================================
背景:本地核查发现 deepseek 平台 600W token 消耗的放大点:
  - sub_tools.py 子 Agent 工具循环 messages 只增不减、零截断
  - supervisor.py 大量问题走 LLM 分解(单请求额外 1~2 次 LLM 调用)
  - chat.py 历史窗口 80K、config.py 步数/上下文上限过高

本脚本对 backend/app 下 4 个文件做精确字符串替换,自动备份,可一键回滚。

用法:
  python token_patch/apply_token_patch.py             # 应用补丁
  python token_patch/apply_token_patch.py --verify    # 只检查当前状态(不修改)
  python token_patch/apply_token_patch.py --rollback  # 从备份恢复
"""

import argparse
import shutil
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

PATCHES: list[dict] = [
    # ═══════════════ 1. config.py — 收紧执行护栏 ═══════════════
    {
        "file": "app/config.py",
        "replaces": [
            # 主步骤上限 40 → 24
            ("max_steps: int = 40", "max_steps: int = 24"),
            # 硬兜底 LLM 轮数 24 → 16
            ("max_tool_rounds: int = 24", "max_tool_rounds: int = 16"),
            # 单次调用最大上下文 64K → 48K
            ("max_context_tokens: int = 64_000", "max_context_tokens: int = 48_000"),
            # 工具输出保护/清理阈值 40K/20K → 24K/12K
            ("tool_output_protect_tokens: int = 40_000", "tool_output_protect_tokens: int = 24_000"),
            ("tool_output_prune_minimum_tokens: int = 20_000", "tool_output_prune_minimum_tokens: int = 12_000"),
        ],
    },
    # ═══════════════ 2. chat.py — 历史窗口收紧 ═══════════════
    {
        "file": "app/api/chat.py",
        "replaces": [
            ("MAX_HISTORY_TOKENS = 80_000", "MAX_HISTORY_TOKENS = 48_000"),
            (
                "# Sliding window: keep up to 80K tokens of history before passing to Agent.",
                "# Sliding window: keep up to 48K tokens of history before passing to Agent.",
            ),
        ],
    },
    # ═══════════════ 3. supervisor.py — 减少 LLM 分解调用 ═══════════════
    {
        "file": "app/agent/supervisor.py",
        "replaces": [
            # 3.1 LLM 分解失败重试 2 → 1 次
            (
                "        for attempt in range(2):",
                "        for attempt in range(1):  # [token 优化] 分解失败重试 2→1,失败直接回退 rag",
            ),
            # 3.2 简短问题(≤24字符)直接走 rag,免 LLM 分解
            (
                "        # ── 默认: 尝试 LLM 分解 ──\n"
                "        return await self._llm_decompose(question, available_agents)",
                "        # ── [token 优化] 简短问题(≤24字符)直接走 rag,免 LLM 分解 ──\n"
                "        if len(question.strip()) <= 24:\n"
                "            return [{\"agent\": \"rag\", \"question\": question}]\n"
                "\n"
                "        # ── 默认: 尝试 LLM 分解 ──\n"
                "        return await self._llm_decompose(question, available_agents)",
            ),
            # 3.3 知识库关键词扩充(提高直接路由命中率)
            (
                '        kb_keywords = [\n'
                '            "文档", "小说", "角色", "对话", "章节", "故事", "内容", "知识库",\n'
                '            "人物", "情节", "书中", "记载", "来源", "character", "dialogue",\n'
                '            "novel", "chapter", "story",\n'
                '        ]',
                '        kb_keywords = [\n'
                '            "文档", "小说", "角色", "对话", "章节", "故事", "内容", "知识库",\n'
                '            "人物", "情节", "书中", "记载", "来源", "character", "dialogue",\n'
                '            "novel", "chapter", "story",\n'
                '            # [token 优化] 扩充\n'
                '            "摘要", "总结", "作者", "主角", "配角", "人物关系", "出场", "设定",\n'
                '            "世界观", "结局", "大意", "简介", "summary", "author", "plot",\n'
                '        ]',
            ),
            # 3.4 代码关键词扩充
            (
                '        code_keywords = [\n'
                '            "代码", "编程", "函数", "bug", "debug", "程序", "算法",\n'
                '            "python", "javascript", "typescript", "前端", "后端",\n'
                '            "code", "function", "programming",\n'
                '        ]',
                '        code_keywords = [\n'
                '            "代码", "编程", "函数", "bug", "debug", "程序", "算法",\n'
                '            "python", "javascript", "typescript", "前端", "后端",\n'
                '            "code", "function", "programming",\n'
                '            # [token 优化] 扩充\n'
                '            "脚本", "接口", "api", "报错", "异常", "重构", "依赖", "配置",\n'
                '            "测试", "部署", "数据库", "sql", "react", "vue", "node", "docker", "git",\n'
                '        ]',
            ),
            # 3.5 网络关键词扩充
            (
                '        web_keywords = [\n'
                '            "新闻", "最新", "天气", "搜索", "查找", "实时",\n'
                '            "news", "weather", "search", "latest", "today",\n'
                '        ]',
                '        web_keywords = [\n'
                '            "新闻", "最新", "天气", "搜索", "查找", "实时",\n'
                '            "news", "weather", "search", "latest", "today",\n'
                '            # [token 优化] 扩充\n'
                '            "热搜", "公告", "发布", "汇率", "股价", "比赛", "比分", "排行榜",\n'
                '            "政策", "法规", "通知", "announcement", "release",\n'
                '        ]',
            ),
        ],
    },
    # ═══════════════ 4. sub_tools.py — 子 Agent 上下文截断 ═══════════════
    {
        "file": "app/agent/sub_tools.py",
        "replaces": [
            # 4.1 工具循环轮数 8 → 5
            ("SUB_AGENT_MAX_ROUNDS = 8", "SUB_AGENT_MAX_ROUNDS = 5"),
            # 4.2 工具结果回传截断 4000 → 1500 字符
            ("_TOOL_RESULT_TRUNC = 4000", "_TOOL_RESULT_TRUNC = 1500"),
            # 4.3 新增 _trim_messages 截断函数(锚点:_AVAILABLE_TOOLS 定义前)
            (
                "_AVAILABLE_TOOLS = (",
                "# ── [token 优化] 子 Agent 上下文截断:控制 tool 循环 context 膨胀 ──\n"
                "_SUB_CTX_MAX_TOKENS = 16_000  # 软上限（估算 token），超出即裁剪最旧轮次\n"
                "_SUB_CTX_KEEP_ROUNDS = 4      # 裁剪时从尾部保留的完整工具轮数\n"
                "\n"
                "\n"
                "def _trim_messages(messages: list[dict]) -> list[dict]:\n"
                "    \"\"\"按估算 token 裁剪 messages；按“轮”丢弃最旧内容，保持 tool_call 配对完整。\"\"\"\n"
                "    def _size(ms: list[dict]) -> int:\n"
                "        return sum(\n"
                "            len(str(m.get(\"content\") or \"\")) + len(str(m.get(\"tool_calls\") or \"\"))\n"
                "            for m in ms\n"
                "        )\n"
                "\n"
                "    if len(messages) <= 2 or _size(messages) <= _SUB_CTX_MAX_TOKENS:\n"
                "        return messages\n"
                "\n"
                "    head = messages[:2]  # system + user 永远保留\n"
                "    tail = messages[2:]\n"
                "    recent: list[dict] = []\n"
                "    rounds = 0\n"
                "    i = len(tail) - 1\n"
                "    while i >= 0 and rounds < _SUB_CTX_KEEP_ROUNDS:\n"
                "        m = tail[i]\n"
                "        recent.append(m)\n"
                "        if m.get(\"role\") == \"assistant\" and m.get(\"tool_calls\"):\n"
                "            rounds += 1\n"
                "        i -= 1\n"
                "    recent.reverse()\n"
                "    trimmed = head + recent\n"
                "    # 极端兜底:仍超限则去掉 tool 消息(此时已不依赖 tool_call 配对)\n"
                "    if _size(trimmed) > _SUB_CTX_MAX_TOKENS:\n"
                "        trimmed = [m for m in trimmed if m.get(\"role\") != \"tool\"]\n"
                "    return trimmed\n"
                "\n"
                "\n"
                "_AVAILABLE_TOOLS = (",
            ),
            # 4.4 每轮循环前裁剪 messages
            (
                "    for rnd in range(1, SUB_AGENT_MAX_ROUNDS + 1):\n"
                "        use_tools = rnd < SUB_AGENT_MAX_ROUNDS",
                "    for rnd in range(1, SUB_AGENT_MAX_ROUNDS + 1):\n"
                "        messages[:] = _trim_messages(messages)  # [token 优化] 每轮前裁剪,防 context 无限膨胀\n"
                "        use_tools = rnd < SUB_AGENT_MAX_ROUNDS",
            ),
        ],
    },
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def apply() -> int:
    """应用所有补丁,返回失败项数量。"""
    failures = 0
    for patch in PATCHES:
        rel = patch["file"]
        path = BACKEND / rel
        if not path.exists():
            print(f"[SKIP] {rel}: 文件不存在")
            failures += 1
            continue
        text = _read(path)
        bak = Path(str(path) + ".bak_token_patch")
        changed = False
        for old, new in patch["replaces"]:
            cnt = text.count(old)
            if cnt == 0:
                print(f"[MISS] {rel}: 未找到匹配 → {old[:60]!r} (可能已应用或源码版本不同)")
                failures += 1
                continue
            if cnt > 1:
                print(f"[SKIP] {rel}: 匹配 {cnt} 次,有歧义 → {old[:60]!r}")
                failures += 1
                continue
            text = text.replace(old, new, 1)
            changed = True
            print(f"[ OK ] {rel}: {old[:44]!r} → {new[:44]!r}")
        if changed:
            if not bak.exists():
                shutil.copy2(path, bak)
                print(f"[BAK ] {rel} → 备份 {bak.name}")
            _write(path, text)
            print(f"[SAVE] {rel} 已写入")
    return failures


def verify() -> None:
    """检查各补丁是否仍处于"未应用"状态(old 仍存在 = 未应用)。"""
    for patch in PATCHES:
        rel = patch["file"]
        path = BACKEND / rel
        if not path.exists():
            print(f"[??] {rel}: 文件不存在")
            continue
        text = _read(path)
        for old, _new in patch["replaces"]:
            cnt = text.count(old)
            status = "未应用" if cnt > 0 else "已应用"
            print(f"[{status}] {rel}: {old[:44]!r} 剩余 {cnt} 次")


def rollback() -> None:
    """从备份恢复所有文件。"""
    restored = 0
    for patch in PATCHES:
        rel = patch["file"]
        path = BACKEND / rel
        bak = Path(str(path) + ".bak_token_patch")
        if bak.exists():
            shutil.copy2(bak, path)
            print(f"[恢复] {rel} ← {bak.name}")
            restored += 1
        else:
            print(f"[跳过] {rel}: 无备份")
    print(f"回滚完成,共恢复 {restored} 个文件。")


def main() -> None:
    parser = argparse.ArgumentParser(description="token 优化补丁:应用/校验/回滚")
    parser.add_argument("--verify", action="store_true", help="只检查状态,不修改")
    parser.add_argument("--rollback", action="store_true", help="从备份恢复")
    args = parser.parse_args()

    if args.rollback:
        rollback()
        return
    if args.verify:
        verify()
        return

    failures = apply()
    print()
    if failures == 0:
        print("✅ 全部补丁应用成功!请重启后端服务(Python 进程)后生效。")
    else:
        print(f"⚠️ 有 {failures} 项未应用(见上方报告)。请检查源码版本是否与补丁基线一致。")


if __name__ == "__main__":
    main()
