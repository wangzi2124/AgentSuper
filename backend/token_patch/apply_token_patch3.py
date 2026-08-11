#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三波 token 优化补丁 v3：系统提示词瘦身 + 工具 schema 瘦身 + 历史上限下调。

改动清单（共 9 项，涉及 2 个文件）:
  S1. app/agent/tools.py : create_skill_tools 技能工具描述截断 200 字符
                          （40 个技能全启用时，完整描述会膨胀每次请求的工具 schema）
  S2. app/agent/tools.py : build_system_prompt_no_kb 技能列表描述压缩为单行、截断 100 字符
  S3. app/agent/tools.py : Instructions 段落压缩（16 行 -> 9 行，细节在工具 schema 中已有）
  S4. app/agent/tools.py : DOCX/PDF/Excel/PPTX 长段落压缩（16 行 -> 2 行）
  S5. app/agent/tools.py : Writing large files + LONG_CONTENT_FILE_RULE 合并（11 行 -> 3 行）
  S6. app/agent/tools.py : Skill loading 段落压缩（6 行 -> 2 行）
  S7. app/agent/tools.py : Order of operations 段落压缩（6 行 -> 2 行）
  S8. app/agent/tools.py : Planning & final report 段落压缩（10 行 -> 2 行）
  S9. app/api/chat.py    : MAX_HISTORY_TOKENS 48K -> 32K（并修正过时注释：graph.py 早已不用 1M 硬编码）

预估收益:
  - 系统提示词正文从 ~3.5KB 降至 ~1.8KB（约 -50%），且对模型行为语义无损
    （被压缩的细节均保留在对应工具 schema / 插件说明中）
  - 技能工具 schema 固定开销大幅下降（40 个技能 × 长描述 -> 40 × ≤200 字符）
  - 每轮对话历史上限 48K -> 32K，多轮会话累计输入下降约 1/3

安全性:
  - 每个替换前先 count 校验：0 次=已应用或版本不符（MISS 报告，不碰文件）；
    >1 次=歧义（SKIP 报告，不碰文件）
  - 应用前自动备份为 *.bak_token_patch3，可随时 --rollback 恢复

用法:
    python token_patch/apply_token_patch3.py            # 应用
    python token_patch/apply_token_patch3.py --verify   # 校验是否已应用
    python token_patch/apply_token_patch3.py --rollback # 回滚到备份
"""
import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_token_patch3"

PATCHES = [
    # ── S1. tools.py: 技能工具 schema 描述截断 200 字符 ──
    (
        "app/agent/tools.py",
        '''    for skill in skill_loader.get_enabled_skills():
        content = skill_loader.get_skill_content(skill.name)
        name = f"load_skill_{skill.name.replace('-', '_').replace(' ', '_')}"
        description = f"Load the '{skill.name}' skill content. Description: {skill.description}"

        def make_skill_fn(n: str, desc: str, c: str) -> ToolDef:''',
        '''    for skill in skill_loader.get_enabled_skills():
        content = skill_loader.get_skill_content(skill.name)
        name = f"load_skill_{skill.name.replace('-', '_').replace(' ', '_')}"
        # [token 优化 v3] 描述截断到 200 字符：40 个技能全启用时避免 schema 体积膨胀（完整描述仍在 SKILL.md）
        _d = " ".join(skill.description.split())
        description = f"Load the '{skill.name}' skill content. Description: {_d[:200]}{'…' if len(_d) > 200 else ''}"

        def make_skill_fn(n: str, desc: str, c: str) -> ToolDef:''',
        "S1 技能工具描述截断 200 字符",
    ),
    # ── S2. tools.py: 系统提示词中技能列表描述单行截断 100 字符 ──
    (
        "app/agent/tools.py",
        '''    if enabled_skills:
        skills_desc = "\\n".join(
            f"   - load_skill_{s.name.replace('-', '_').replace(' ', '_')}() - Load '{s.name}' skill: {s.description}"
            for s in enabled_skills
        )
        tool_parts.append(f"Skill tools (load skill files):\\n{skills_desc}")''',
        '''    if enabled_skills:
        # [token 优化 v3] 描述压缩为单行、截断 100 字符（完整描述见对应工具 schema）
        def _short(desc: str, limit: int = 100) -> str:
            d = " ".join(desc.split())
            return d[:limit] + ("…" if len(d) > limit else "")
        skills_desc = "\\n".join(
            f"   - load_skill_{s.name.replace('-', '_').replace(' ', '_')}() - {_short(s.description)}"
            for s in enabled_skills
        )
        tool_parts.append(f"Skill tools (load skill files):\\n{skills_desc}")''',
        "S2 技能列表描述单行截断 100 字符",
    ),
    # ── S3. tools.py: Instructions 段落压缩 ──
    (
        "app/agent/tools.py",
        '''        "",
        "Instructions:",
        "- There is no knowledge base available (no documents uploaded).",
        "- Answer based on your own knowledge.",
        "- If you don't know something, say so honestly.",
        "- Only call tools that are directly relevant to the user's request. Do NOT call unrelated tools.",
        "- For document generation, use the docx-generator plugin (plugin_docx-generator_tool_create_docx) for .docx files, or save content using tool_write_file for other formats.",
        "- For web search (查找信息/搜索), use plugin_internet-search_tool_internet_search.",
        "  - Use region='cn' for Chinese content, region='global' for international.",
        "  - Use engine='auto' to auto-select. Engines: tavily (requires TAVILY_API_KEY), bing (requires BING_API_KEY), duckduckgo (free). Avoid 'baidu' (anti-bot, usually returns nothing).",
        "- For fetching content from a specific URL (查看某个网站的内容), use plugin_internet-search_tool_extract_urls.",
        "- For HTTP requests (testing APIs, calling endpoints, fetching data from URLs), use plugin_http-client_tool_http_request, plugin_http-client_tool_http_get, or plugin_http-client_tool_http_post.",
        "  - Pass headers as a JSON string, e.g. {\\"Authorization\\": \\"Bearer xxx\\"}.",
        "  - Pass body as a JSON string for JSON requests, or key=value&key2=value2 for form data.",
        "  - For simple GET requests, prefer plugin_http-client_tool_http_get.",
        "  - For simple POST with JSON body, prefer plugin_http-client_tool_http_post.",
        "- CRITICAL: tool_execute is ONLY for building/testing projects (npm install, npm run build, etc.). NEVER use tool_execute for curl, wget, ping, or any network/web operations. Use the http-client plugin instead.",
        "- For knowledge base export, use kb-export tools.",
        "- If the user's request doesn't match any tool's purpose, answer directly without calling tools.",''',
        '''        "",
        "Instructions:",
        "- No knowledge base available; answer from your own knowledge; say so honestly if unsure.",
        "- Only call tools directly relevant to the request.",
        "- Docs: use docx/pdf/excel/pptx generator plugins, or tool_write_file for other formats.",
        "- Web search: plugin_internet-search_tool_internet_search (region='cn'|'global').",
        "- URL content: plugin_internet-search_tool_extract_urls.",
        "- HTTP: plugin_http-client_tool_http_get/_post/_request (headers as JSON string).",
        "- CRITICAL: tool_execute ONLY for build/install (npm install, npm run build). NEVER for curl/wget/ping/network — use http-client plugin.",
        "- KB export: kb-export tools.",
        "- If no tool fits, answer directly without calling tools.",''',
        "S3 Instructions 段落 16 行 -> 9 行",
    ),
    # ── S4. tools.py: DOCX/PDF/Excel/PPTX 段落压缩 ──
    (
        "app/agent/tools.py",
        '''        "",
        "IMPORTANT - DOCX / PDF / Excel / PPTX document creation:",
        "  When the user asks to create a Word document (.docx):",
        "  - Use the docx-generator plugin (plugin_docx-generator_tool_create_docx).",
        "    sections JSON supports: heading, paragraph, table, bullet_list.",
        "  When the user asks to create a PDF document (.pdf):",
        "  - Use the pdf-generator plugin (plugin_pdf-generator_tool_create_pdf).",
        "    sections JSON supports: heading, paragraph, table, bullet_list, horizontal_rule.",
        "  When the user asks to create an Excel spreadsheet (.xlsx):",
        "  - Use the excel-generator plugin (plugin_excel-generator_tool_create_excel).",
        "    sheets JSON supports: name, headers, rows. Supports multiple sheets.",
        "  When the user asks to create a PowerPoint presentation (.pptx):",
        "  - Use the pptx-generator plugin (plugin_pptx-generator_tool_create_pptx).",
        "    slides JSON supports types: title, section_header, content, two_column, table.",
        "    Each slide supports optional bg_color and font_color.",
        "  Files are saved per the user's specified directory rules.",''',
        '''        "",
        "IMPORTANT - Documents (.docx/.pdf/.xlsx/.pptx): use the matching generator plugin "
        "(docx/pdf/excel/pptx-generator); section/slide schemas are in each tool's description. Files saved per your directory rules.",''',
        "S4 文档生成段落 16 行 -> 2 行",
    ),
    # ── S5. tools.py: Writing large files + LONG_CONTENT_FILE_RULE 合并 ──
    (
        "app/agent/tools.py",
        '''        "",
        "IMPORTANT - Writing large files:",
        "  A single tool call cannot carry very large content (LLM output is limited). For content larger than",
        "  roughly 6KB (about 150 lines), do NOT try to write it all at once:",
        "  1. Use tool_write_file(path, content, overwrite=True) to write the first chunk.",
        "  2. Then append the remaining chunks with tool_append_file(path, content), one chunk per call.",
        "  This avoids truncated/corrupted files. You may also use tool_read_file to verify after writing.",
        "",
        LONG_CONTENT_FILE_RULE,
        "",''',
        '''        "",
        "IMPORTANT - Long content MUST be written to files, NOT pasted in replies (≈500 Chinese chars / 1000 tokens):",
        "  Write text/code with tool_write_file (first chunk, then tool_append_file for large files); generator plugins for .docx/.pdf/.xlsx/.pptx.",
        "  Reply with only: saved path + summary + structure. No 'please verify' closing remarks.",
        "",''',
        "S5 长内容规则 11 行 -> 3 行（LONG_CONTENT_FILE_RULE 常量保留供 graph.py 引用）",
    ),
    # ── S6. tools.py: Skill loading 段落压缩 ──
    (
        "app/agent/tools.py",
        '''        "",
        "IMPORTANT - Skill loading before code/design tasks:",
        "  When the user asks to create code, web pages, apps, or designs:",
        "  1. FIRST, call the relevant load_skill_*() tool to get specialized instructions and best practices.",
        "  2. Then follow the skill's guidance to write files using tool_write_file.",
        "  3. Only run tool_execute for build/install if the skill instructs you to.",
        "",''',
        '''        "",
        "IMPORTANT - Before code/design tasks: FIRST call the relevant load_skill_*() tool for best practices, "
        "then write files with tool_write_file; tool_execute only for build/install if the skill says so.",
        "",''',
        "S6 Skill loading 段落 6 行 -> 2 行",
    ),
    # ── S7. tools.py: Order of operations 段落压缩 ──
    (
        "app/agent/tools.py",
        '''        "",
        "IMPORTANT - Order of operations for creating projects:",
        "  1. FIRST, write ALL necessary code files using tool_write_file (it auto-creates directories).",
        "  2. Do NOT use tool_execute with mkdir or Set-Content to create files — use tool_write_file instead.",
        "  3. ONLY AFTER all files are written, run tool_execute for npm install or build if needed.",
        "  Do NOT run 'npm create', 'npx create-react-app', 'npm create vite' etc. Write files manually.",
        "",''',
        '''        "",
        "IMPORTANT - Projects: write ALL code files first via tool_write_file (auto-creates dirs); do NOT use "
        "mkdir/Set-Content or 'npm create'/'create-react-app'/'create vite'; only then run tool_execute for install/build.",
        "",''',
        "S7 项目创建顺序 6 行 -> 2 行",
    ),
    # ── S8. tools.py: Planning & final report 段落压缩 ──
    (
        "app/agent/tools.py",
        '''        "",
        "IMPORTANT - Planning & final report for multi-step tasks:",
        "  When the task needs multiple steps or multiple files (e.g. building a project):",
        "  1. FIRST output a short plan block marked '## 实施计划' listing the steps as a checklist.",
        "  2. As you work, keep the plan visible and mark each step's progress.",
        "  3. ALWAYS end your final answer with a '## 完成情况' section listing:",
        "     - 已完成 (what was completed)",
        "     - 未完成 (what was NOT completed, if any)",
        "     - 下一步 (concrete next steps to finish the task)",
        "  4. When the step limit is reached, you MUST give this report without calling more tools.",''',
        '''        "",
        "IMPORTANT - Multi-step tasks: start with plan block '## 实施计划'; end with '## 完成情况' "
        "(已完成 / 未完成 / 下一步). At step limit, give this report without calling more tools.",''',
        "S8 Planning & report 10 行 -> 2 行",
    ),
    # ── S9. chat.py: 历史上限 48K -> 32K + 注释修正 ──
    (
        "app/api/chat.py",
        '''# Sliding window: keep up to 48K tokens of history before passing to Agent.
# The Agent internally truncates to 1M tokens in graph.py, so this threshold
# is just for DB storage efficiency, not for context management.
MAX_HISTORY_TOKENS = 48_000''',
        '''# [token 优化 v3] Sliding window: keep up to 32K tokens of history before passing to Agent.
# graph.py 通过 config.max_context_tokens(48K) 做上下文管理，此阈值仅控制历史注入量。
MAX_HISTORY_TOKENS = 32_000''',
        "S9 历史上限 48K -> 32K",
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
    print(f"== 应用第三波补丁（backend 根: {BACKEND_ROOT}）==")
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
    print(f"== 校验第三波补丁应用状态（backend 根: {BACKEND_ROOT}）==")
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
    print(f"== 回滚第三波补丁（backend 根: {BACKEND_ROOT}）==")
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
    parser = argparse.ArgumentParser(description="第三波 token 优化补丁")
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
