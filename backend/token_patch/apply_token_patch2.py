#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二波 token 优化补丁 v2：系统提示词缓存命中 + 子 Agent 上下文收紧 + RAG 精简。

改动清单（共 6 项，涉及 2 个文件）:
  A1. app/agent/graph.py : RAG 检索上下文从 system 消息移出 → system 完全稳定
                          → 命中 DeepSeek 前缀缓存（命中 token 按 0.1x 计费）
                          → 同时保留 _system_prompt_with_kb 方法定义（不再调用，无副作用）
  A2. app/agent/graph.py : RAG 上下文改拼到 user 消息前缀（配合 A1，行为等价，缓存收益最大）
  B.  app/agent/graph.py : 检索片段数 k=5 -> k=3（每次检索少注入约 2 段文档内容）
  C1. app/agent/sub_tools.py : 子 Agent 工具轮数 5 -> 4（3 轮工具 + 1 次收尾）
  C2. app/agent/sub_tools.py : 子 Agent 上下文软上限 16K -> 12K
  C3. app/agent/sub_tools.py : 裁剪保留轮数 4 -> 3

安全性:
  - 每个替换前先 count 校验：0 次=已应用或版本不符（MISS 报告，不碰文件）；
    >1 次=歧义（SKIP 报告，不碰文件）
  - 应用前自动备份为 *.bak_token_patch2，可随时 --rollback 恢复

用法:
    python token_patch/apply_token_patch2.py            # 应用
    python token_patch/apply_token_patch2.py --verify   # 校验是否已应用
    python token_patch/apply_token_patch2.py --rollback # 回滚到备份
"""
import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_token_patch2"

# (相对路径, 旧串, 新串, 说明)
PATCHES = [
    # ── A1. graph.py: system 稳定化（核心，缓存收益最大）──
    (
        "app/agent/graph.py",
        '''        if state["context"]:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\\n\\n".join(context_parts)
            full_system_prompt = (
                self._system_prompt_with_kb()
                + "\\n\\n"
                + f"Retrieved Context:\\n{context_text}"
            )
        else:
            full_system_prompt = self.system_prompt''',
        '''        context_text = ""
        if state["context"]:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\\n\\n".join(context_parts)
        # [token 优化 v2] system 保持完全稳定 → 最大化 DeepSeek 前缀缓存命中（命中按 0.1x 计费）
        # RAG 检索结果改放 user 消息前缀（见下方 user 消息构建），避免 system 每次变化导致缓存整体失效。
        full_system_prompt = self.system_prompt''',
        "A1 system 稳定化：RAG context 移出 system 消息",
    ),
    # ── A2. graph.py: RAG 上下文拼到 user 消息前缀 ──
    (
        "app/agent/graph.py",
        '''        # Build user content: text only or multimodal if files attached
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": state["question"]}]
            for f in user_files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": state["question"]})''',
        '''        # Build user content: text only or multimodal if files attached
        # [token 优化 v2] RAG 上下文改放 user 消息前缀（system 保持稳定 → 命中前缀缓存）
        user_question = (
            f"Retrieved Context:\\n{context_text}\\n\\n---\\n\\n{state['question']}"
            if context_text else state["question"]
        )
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": user_question}]
            for f in user_files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_question})''',
        "A2 RAG 上下文移到 user 消息前缀",
    ),
    # ── B. graph.py: RAG 检索片段数 5 -> 3 ──
    (
        "app/agent/graph.py",
        '''            results = await asyncio.to_thread(
                functools.partial(self.retriever.invoke, state["question"], k=5)
            )''',
        '''            results = await asyncio.to_thread(
                functools.partial(self.retriever.invoke, state["question"], k=3)  # [token 优化 v2] 5->3
            )''',
        "B RAG 检索 k=5 -> k=3",
    ),
    # ── C1. sub_tools.py: 工具轮数 5 -> 4 ──
    (
        "app/agent/sub_tools.py",
        "SUB_AGENT_MAX_ROUNDS = 5",
        "SUB_AGENT_MAX_ROUNDS = 4  # [token 优化 v2] 5->4（3 轮工具 + 1 次收尾）",
        "C1 子 Agent 工具轮数 5 -> 4",
    ),
    # ── C2. sub_tools.py: 上下文软上限 16K -> 12K ──
    (
        "app/agent/sub_tools.py",
        "_SUB_CTX_MAX_TOKENS = 16_000  # 软上限（估算 token），超出即裁剪最旧轮次",
        "_SUB_CTX_MAX_TOKENS = 12_000  # [token 优化 v2] 软上限 16K->12K，进一步压缩 context 膨胀",
        "C2 子 Agent 上下文软上限 16K -> 12K",
    ),
    # ── C3. sub_tools.py: 保留轮数 4 -> 3 ──
    (
        "app/agent/sub_tools.py",
        "_SUB_CTX_KEEP_ROUNDS = 4      # 裁剪时从尾部保留的完整工具轮数",
        "_SUB_CTX_KEEP_ROUNDS = 3      # [token 优化 v2] 保留轮数 4->3",
        "C3 子 Agent 裁剪保留轮数 4 -> 3",
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
    print(f"== 应用第二波补丁（backend 根: {BACKEND_ROOT}）==")
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
    print(f"== 校验第二波补丁应用状态（backend 根: {BACKEND_ROOT}）==")
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
    print(f"== 回滚第二波补丁（backend 根: {BACKEND_ROOT}）==")
    n = 0
    for rel, old, new, label in PATCHES:
        path = BACKEND_ROOT / rel
        bak = path.with_name(path.name + BAK_SUFFIX)
        if bak.exists():
            _write(path, _read(bak))
            bak.unlink()
            print(f"  [ROLLBACK] {rel}: {label}")
            n += 1
    print(f"== 回滚完成: 恢复 {n} 个文件 ==")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="第二波 token 优化补丁 v2")
    parser.add_argument("--verify", action="store_true", help="校验应用状态")
    parser.add_argument("--rollback", action="store_true", help="回滚到备份")
    args = parser.parse_args()
    if args.rollback:
        return rollback()
    if args.verify:
        return verify()
    return apply()


if __name__ == "__main__":
    sys.exit(main())
