#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第五波 token 优化补丁 v5：单次调用基数降本 —— 上下文上限收紧 + 工具 schema 按需挂载 + 天气/台风结果截断。

背景（monitor_stats 实测：1 次对话 11 次调用烧 34.96 万 prompt token，平均每次 3.2 万）:
  一次主循环调用的成本 ≈ system prompt + 全部工具 schema + 累积历史 三者全量重发。
  优化前每次调用 3.2 万 ≈ schema 8-12K（40 技能 + 13 插件全量）+ 历史顶满 48K。

改动清单（共 9 项，涉及 2 个文件）:
  P5-1. app/config.py          : max_context_tokens 48K → 32K（配合已有 v4 压缩，信息不丢，
                                  单次调用天花板直接 -33%）。
  P5-2. app/agent/graph.py     : _build_tool_defs 改为"按需挂载"——核心文件工具常驻，
                                  技能/插件工具按 意图关键词命中 + 已使用保留 才挂 schema。
                                  system prompt 已列出全部工具名，模型知道所有工具存在；
                                  若模型调用了未挂载工具，_execute_tool 仍可执行（self.tools 全量）。
                                  schema 固定开销 8-12K → 2-4K。
  P5-3. app/agent/graph.py     : 新增 _bound_plugin_result()：天气/台风类插件结果 >1500 字符截断，
                                  避免整块结构化数据躺进历史每轮重发。
  P5-4/5. app/agent/graph.py   : _execute_tool 两处 return 接 _bound_plugin_result。
  P5-6. app/agent/graph.py     : 首轮 tool_defs 构建传入 question（关键词筛选用）。
  P5-7/8. app/agent/graph.py   : used_tools 集合初始化 + 循环内累积已使用工具名。
  P5-9. app/agent/graph.py     : 工具循环内每轮按需重挂载（核心 + 意图命中 + 已使用保留）。

预估收益:
  - 单次对话 35 万 → 约 20-23 万（每次调用基数 -30~40%）
  - 多轮工具循环（读文件类任务）收益最大：schema 不再每轮全量重发

安全性:
  - 每个替换前先 count 校验：0 次=已应用或版本不符（MISS 报告，不碰文件）；
    >1 次=歧义（SKIP 报告，不碰文件）
  - 应用前自动备份为 *.bak_token_patch5，可随时 --rollback 恢复
  - 新增/修改均带 [token 优化 v5] 注释，可 grep 定位

用法:
    python token_patch/apply_token_patch5.py            # 应用
    python token_patch/apply_token_patch5.py --verify   # 校验是否已应用
    python token_patch/apply_token_patch5.py --rollback # 回滚到备份
"""
import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_token_patch5"

PATCHES = [
    # ── P5-1. config.py: 上下文上限 48K → 32K ──
    (
        "app/config.py",
        '''    # Token 成本控制
    # 每次 LLM 调用允许的最大上下文（system + history + 当前问题）
    # 对齐 opencode overflow.ts：usable = max_context_tokens - context_reserve_tokens
    max_context_tokens: int = 48_000''',
        '''    # Token 成本控制
    # 每次 LLM 调用允许的最大上下文（system + history + 当前问题）
    # 对齐 opencode overflow.ts：usable = max_context_tokens - context_reserve_tokens
    # [token 优化 v5] 48K → 32K：配合 v4 压缩（信息不丢），单次调用天花板 -33%
    max_context_tokens: int = 32_000''',
        "P5-1 max_context_tokens 48K→32K",
    ),
    # ── P5-2. graph.py: _build_tool_defs 改为按需挂载（含类属性意图路由表） ──
    (
        "app/agent/graph.py",
        '''    def _build_tool_defs(self) -> list[dict] | None:
        """构建OpenAI格式的工具定义列表。"""
        if not self.tools:
            return None
        return [t.to_openai_tool() for t in self.tools]''',
        '''    # [token 优化 v5] 按需挂载工具 schema：核心文件工具常驻，技能/插件按意图关键词 + 已使用保留
    _CORE_TOOL_PREFIXES = ("tool_",)
    _WEATHER_TOOL_PREFIXES = ("plugin_weather", "plugin_weather-alert")
    _WEATHER_RESULT_LIMIT = 1500  # 字符
    # 意图关键词 → 需要挂载的工具名前缀（任一词命中即挂载该类工具）
    _INTENT_RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (("天气", "台风", "气象", "温度", "降雨", "下雪", "weather", "typhoon", "forecast"),
         ("plugin_weather", "plugin_weather-alert")),
        (("文档", "word", "docx", "pdf", "excel", "xlsx", "ppt", "pptx", "表格", "幻灯片", "报告"),
         ("plugin_docx-generator", "plugin_pdf-generator", "plugin_excel-generator", "plugin_pptx-generator",
          "load_skill_docx", "load_skill_pdf", "load_skill_xlsx", "load_skill_pptx", "load_skill_doc_coauthoring")),
        (("网页", "前端", "react", "vue", "html", "css", "网站", "页面", "artifact", "frontend", "web"),
         ("load_skill_frontend_design", "load_skill_web_artifacts_builder", "load_skill_webapp_testing",
          "load_skill_theme_factory", "load_skill_canvas_design")),
        (("搜索", "查一下", "新闻", "资讯", "上网", "search", "news", "internet"),
         ("plugin_internet-search_",)),
        (("图片", "海报", "设计", "艺术", "绘图", "生成图", "image", "poster", "art", "draw"),
         ("load_skill_canvas_design", "load_skill_algorithmic_art", "load_skill_slack_gif_creator")),
        (("语音", "声音", "配音", "克隆", "合成", "voice", "audio", "speech"),
         ("plugin_voice-clone_",)),
        (("角色", "人物", "对话", "台词", "character", "dialogue"),
         ("plugin_character-analysis_",)),
        (("知识库", "kb", "导出"),
         ("plugin_kb-export_",)),
        (("代码", "编程", "bug", "调试", "重构", "code", "debug", "test", "tdd", "review", "实现"),
         ("load_skill_tdd", "load_skill_code_review", "load_skill_diagnosing_bugs", "load_skill_implement",
          "load_skill_to_tickets", "load_skill_grilling", "load_skill_grill_me", "load_skill_codebase_design")),
        (("技能", "skill"),
         ("load_skill_",)),
        (("插件", "plugin"),
         ("plugin_",)),
        (("教学", "学习", "teach"),
         ("load_skill_teach",)),
        (("研究", "research"),
         ("load_skill_research",)),
        (("模型", "api", "claude", "大模型"),
         ("load_skill_claude_api",)),
        (("架构", "模块", "设计模式", "architecture"),
         ("load_skill_codebase_design", "load_skill_domain_modeling", "load_skill_improve_codebase_architecture")),
    ]

    def _tool_matches_intent(self, t: ToolDef, question_lower: str) -> bool:
        """意图关键词命中：问题包含关键词且工具名前缀匹配 → 挂载该工具 schema。"""
        for keywords, prefixes in self._INTENT_RULES:
            if not any(k in question_lower for k in keywords):
                continue
            if t.name.startswith(prefixes):
                return True
        return False

    def _build_tool_defs(self, question: str = "", used_names: set | None = None) -> list[dict] | None:
        """[token 优化 v5] 按需挂载 OpenAI 工具定义。

        system prompt 已列出全部工具名（模型知道所有工具存在），此处只把本轮可能用到的
        schema 发给 LLM：核心文件工具常驻 + 意图关键词命中 + 已使用工具保留。
        schema 固定开销从 8-12K 降到 2-4K。若模型调用了未挂载工具，
        _execute_tool 仍可执行（self.tools 全量），下一轮该工具自动保留。
        """
        if not self.tools:
            return None
        used = used_names or set()
        q = (question or "").lower()
        selected: list[ToolDef] = []
        for t in self.tools:
            if t.name in used:
                selected.append(t)
                continue
            if t.name.startswith(self._CORE_TOOL_PREFIXES):
                selected.append(t)
                continue
            if self._tool_matches_intent(t, q):
                selected.append(t)
                continue
        return [t.to_openai_tool() for t in selected]''',
        "P5-2 _build_tool_defs 按需挂载",
    ),
    # ── P5-3. graph.py: _execute_tool 前插入 _bound_plugin_result ──
    (
        "app/agent/graph.py",
        '''    async def _execute_tool(self, name: str, args: dict, state: dict | None = None) -> str:
        """执行指定的工具函数，处理权限检查和错误。"""
        for t in self.tools:
            if t.name == name:''',
        '''    def _bound_plugin_result(self, name: str, result: str) -> str:
        """[token 优化 v5] 大块结构化插件结果（天气/台风）截断，避免整块数据躺进历史每轮重发。"""
        if name.startswith(self._WEATHER_TOOL_PREFIXES) and len(result) > self._WEATHER_RESULT_LIMIT:
            return result[:self._WEATHER_RESULT_LIMIT] + "\\n…[已截断：天气/台风数据过长，仅保留前 1500 字符]"
        return result

    async def _execute_tool(self, name: str, args: dict, state: dict | None = None) -> str:
        """执行指定的工具函数，处理权限检查和错误。"""
        for t in self.tools:
            if t.name == name:''',
        "P5-3 新增 _bound_plugin_result",
    ),
    # ── P5-4. graph.py: _execute_tool 正常路径返回接截断（20 空格缩进） ──
    (
        "app/agent/graph.py",
        '''                    result = await asyncio.to_thread(t.fn, **args)
                    return str(result)''',
        '''                    result = await asyncio.to_thread(t.fn, **args)
                    return self._bound_plugin_result(name, str(result))''',
        "P5-4 _execute_tool 正常路径结果截断",
    ),
    # ── P5-5. graph.py: _execute_tool 权限放行路径返回接截断（24 空格缩进） ──
    (
        "app/agent/graph.py",
        '''                        result = await asyncio.to_thread(t.fn, **args)
                        return str(result)''',
        '''                        result = await asyncio.to_thread(t.fn, **args)
                        return self._bound_plugin_result(name, str(result))''',
        "P5-5 _execute_tool 权限放行路径结果截断",
    ),
    # ── P5-6. graph.py: 首轮 tool_defs 构建传入 question ──
    (
        "app/agent/graph.py",
        '''        tool_defs = self._build_tool_defs()

        messages = [''',
        '''        # [token 优化 v5] 按需挂载：首轮按问题关键词筛选工具 schema
        tool_defs = self._build_tool_defs(state.get("question", ""))

        messages = [''',
        "P5-6 首轮按问题关键词构建 tool_defs",
    ),
    # ── P5-7. graph.py: used_tools 集合初始化 ──
    (
        "app/agent/graph.py",
        '''        rounds = 0
        tool_calls_count = 0''',
        '''        rounds = 0
        tool_calls_count = 0
        # [token 优化 v5] 已使用工具集合：每轮重挂载时保留，避免模型想复用却被移除
        used_tools: set[str] = set()''',
        "P5-7 used_tools 初始化",
    ),
    # ── P5-8. graph.py: 循环内累积已使用工具名 ──
    (
        "app/agent/graph.py",
        '''            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}''',
        '''            for tc in msg.tool_calls:
                tool_name = tc.function.name
                # [token 优化 v5] 记录已使用工具 → 下轮重挂载时保留
                used_tools.add(tool_name)
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}''',
        "P5-8 循环内累积 used_tools",
    ),
    # ── P5-9. graph.py: 工具循环内每轮按需重挂载 ──
    (
        "app/agent/graph.py",
        '''            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))
            # MAX_STEPS 注入后不再允许继续调用工具（对齐 opencode max-steps.ts 的 disable-tools 语义）
            final_tool_defs = None if steps_prompt_injected else tool_defs''',
        '''            messages = sanitize_tool_messages(_truncate_messages(messages, max_tokens=usable_context_tokens(), reserve_tokens=0))
            # [token 优化 v5] 每轮按需重挂载（核心常驻 + 意图命中 + 已使用保留），schema 固定开销大降
            tool_defs = self._build_tool_defs(state.get("question", ""), used_tools)
            # MAX_STEPS 注入后不再允许继续调用工具（对齐 opencode max-steps.ts 的 disable-tools 语义）
            final_tool_defs = None if steps_prompt_injected else tool_defs''',
        "P5-9 循环内每轮重挂载 tool_defs",
    ),
]


def _target_path(rel: str) -> Path:
    return BACKEND_ROOT / rel


def _read(rel: str) -> str:
    return _target_path(rel).read_text(encoding="utf-8")


def _write(rel: str, text: str) -> None:
    _target_path(rel).write_text(text, encoding="utf-8")


def _backup(rel: str) -> Path:
    bak = _target_path(rel + BAK_SUFFIX)
    if not bak.exists():
        shutil.copy2(_target_path(rel), bak)
    return bak


def apply() -> None:
    print("=" * 70)
    print("PATCH5 应用开始（共 %d 项）" % len(PATCHES))
    print("=" * 70)
    ok = miss = skip = 0
    for rel, old, new, desc in PATCHES:
        try:
            text = _read(rel)
        except FileNotFoundError:
            print("[MISS] %s 文件不存在，跳过" % rel)
            miss += 1
            continue
        cnt = text.count(old)
        if cnt == 0:
            print("[MISS] %s | %s —— 未找到（已应用或版本不同）" % (rel, desc))
            miss += 1
            continue
        if cnt > 1:
            print("[SKIP] %s | %s —— 匹配 %d 处，歧义，不碰文件" % (rel, desc, cnt))
            skip += 1
            continue
        _backup(rel)
        _write(rel, text.replace(old, new, 1))
        print("[ OK ] %s | %s" % (rel, desc))
        ok += 1
    print("-" * 70)
    print("结果: OK=%d  MISS=%d  SKIP=%d" % (ok, miss, skip))
    if ok:
        print("备份已生成: *.%s （回滚用: python %s --rollback）" % (BAK_SUFFIX.lstrip("."), Path(__file__).name))
    if miss or skip:
        print("提示: 有 MISS/SKIP 项，请把上面输出贴给助手核对源码版本。")


def verify() -> None:
    print("=" * 70)
    print("PATCH5 校验（检查各 old 文本是否已被替换）")
    print("=" * 70)
    ok = miss = 0
    # 注意：部分补丁是前缀式 new = old + 新增行（P5-3/P5-7），old 应用后仍在文件中，
    # 因此先看 new 是否已存在，其次才看 old 是否被整体替换。
    for rel, old, new, desc in PATCHES:
        try:
            text = _read(rel)
        except FileNotFoundError:
            print("[MISS] %s 文件不存在" % rel)
            miss += 1
            continue
        if new in text:
            print("[ OK ] %s | %s（new 已存在 → 已应用）" % (rel, desc))
            ok += 1
        elif old not in text:
            print("[ OK ] %s | %s（old 已不存在 → 已应用）" % (rel, desc))
            ok += 1
        else:
            print("[MISS] %s | %s —— new 未出现且 old 仍存在（未应用或版本不同）" % (rel, desc))
            miss += 1
    print("-" * 70)
    print("结果: OK=%d  MISS=%d" % (ok, miss))


def rollback() -> None:
    print("=" * 70)
    print("PATCH5 回滚")
    print("=" * 70)
    rels = sorted({rel for rel, _, _, _ in PATCHES})
    for rel in rels:
        bak = _target_path(rel + BAK_SUFFIX)
        if not bak.exists():
            print("[SKIP] %s 无备份" % rel)
            continue
        shutil.copy2(bak, _target_path(rel))
        print("[ OK ] %s 已恢复备份" % rel)
    print("完成。若仍需恢复更早版本，可用 .bak_token_patch4 等旧备份手动处理。")


def main() -> None:
    parser = argparse.ArgumentParser(description="第五波 token 优化补丁 v5")
    parser.add_argument("--verify", action="store_true", help="仅校验是否已应用")
    parser.add_argument("--rollback", action="store_true", help="回滚到备份")
    args = parser.parse_args()
    if args.rollback:
        rollback()
        return
    if args.verify:
        verify()
        return
    apply()


if __name__ == "__main__":
    main()
