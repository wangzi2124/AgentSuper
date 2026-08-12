#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第八波补丁 v8（prompt log v1）：每次调用模型前把提示词写入日志。

背景:
  需要审计「每次调用模型时发送的提示词内容」。本波在模型调用入口埋点：
  把本轮 messages（提示词）以 JSON Lines 追加写入 <backend>/log/ 目录，
  文件名 = 调用类型 + 时间（如 graph.llm_call_20260812_213045_123.jsonl）。

改动:
  新文件 app/prompt_log.py : log_prompt() 单例（线程安全、失败静默、自动建目录）
  app/agent/graph.py       : 2 处（import + _llm_call 开头；主 Agent 每轮 LLM 调用统一埋点，
                             覆盖入口轮 / 工具循环轮 / max-steps 收尾轮）
  app/agent/code_agent.py  : 2 处（import + _ask_llm 开头；代码助手 LLM 调用）

用法:
    python token_patch/add_prompt_logging.py            # 应用
    python token_patch/add_prompt_logging.py --verify   # 校验
    python token_patch/add_prompt_logging.py --rollback # 回滚

安全性:
  - 每条替换 count 校验：0=MISS（已应用或版本不符，不碰文件）、>1=SKIP（歧义，不碰文件）
  - 应用前自动备份 *.bak_prompt_log
  - 新文件幂等：已存在则 SKIP

说明:
  - 日志目录默认 <backend>/log（自动创建）；设置 AGENTSUPER_LOG_DIR 环境变量则优先
    （与 trace_log.py 的覆盖机制一致，便于部署统一收日志）。
  - call_type 示例: graph.llm_call（主 Agent）、code_agent.ask_llm（代码助手）。
"""
import argparse
import shutil
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BAK_SUFFIX = ".bak_prompt_log"

# ── 新文件（幂等：已存在则 SKIP）──
NEW_FILES: list[tuple[str, str]] = []

_PROMPT_LOG_PY = '''# -*- coding: utf-8 -*-
"""模型调用提示词日志 [prompt log v1]。

每次调用 LLM 前，把本轮提示词（messages）以 JSON Lines 追加写入
<backend>/log/{call_type}_{YYYYMMDD_HHMMSS_mmm}.jsonl：
文件名 = 调用类型 + 时间（毫秒级后缀避免同秒冲突）。

- 日志目录默认 <backend>/log（自动创建）；若设置了 AGENTSUPER_LOG_DIR 环境变量
  则优先使用（与 trace_log.py 的覆盖机制保持一致，便于部署统一收日志）。
- 线程安全（threading.Lock）、失败静默，绝不影响主流程。
- call_type 示例: graph.llm_call（主 Agent 每轮 LLM 调用）、
  code_agent.ask_llm（代码助手）。

记录字段:
  ts        调用时刻（epoch 秒，3 位小数）
  call_type 调用类型（即文件名前缀）
  messages  本轮完整提示词消息列表
  model     模型名（extra 传入）
  tool_count 本轮挂载工具数（extra 传入）
"""
import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_BASE = Path(__file__).resolve().parents[1] / "log"  # <backend>/log


def _log_path(call_type: str) -> Path:
    base = os.environ.get("AGENTSUPER_LOG_DIR", str(_BASE))
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (int(time.time() * 1000) % 1000)
    return d / f"{call_type}_{stamp}.jsonl"


def log_prompt(call_type: str, messages: list, **extra) -> None:
    """调用模型前记录提示词；任何失败静默忽略，绝不影响主流程。"""
    try:
        rec = {"ts": round(time.time(), 3), "call_type": call_type, "messages": messages}
        rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            with open(_log_path(call_type), "a", encoding="utf-8") as f:
                f.write(line + "\\n")
    except Exception:
        pass
'''

NEW_FILES.append(("app/prompt_log.py", _PROMPT_LOG_PY))

# ── 插入点补丁 ──
PATCHES: list[dict] = [
    # G1 import（锚定 v7 trace 导入行，确保唯一）
    dict(
        file="app/agent/graph.py",
        old="from app.trace_log import trace, trace_messages  # [token trace v7]",
        new="from app.trace_log import trace, trace_messages  # [token trace v7]\nfrom app.prompt_log import log_prompt  # [prompt log v1]",
        desc="G1: graph.py 导入 log_prompt",
    ),
    # G2 _llm_call 开头：主 Agent 所有 LLM 调用统一埋点
    # （入口轮 803 / 工具循环轮 965 / max-steps 收尾轮 1033 都经 _llm_call）
    # 锚定 from types import SimpleNamespace（仅 _llm_call 内出现一次）消歧
    dict(
        file="app/agent/graph.py",
        old="        from types import SimpleNamespace\n\n        start = tmod.time()",
        new="        from types import SimpleNamespace\n\n        start = tmod.time()\n        log_prompt(\"graph.llm_call\", messages, model=model, tool_count=len(tool_defs or []))  # [prompt log v1]",
        desc="G2: _llm_call 开头记录本轮提示词",
    ),
    # C1 import（code_agent.py 中该行仅一处）
    dict(
        file="app/agent/code_agent.py",
        old="from app.monitor import record_model_call",
        new="from app.monitor import record_model_call\nfrom app.prompt_log import log_prompt  # [prompt log v1]",
        desc="C1: code_agent.py 导入 log_prompt",
    ),
    # C2 _ask_llm：把内联 messages 提取为变量并记录（该文件仅此一处 acompletion）
    dict(
        file="app/agent/code_agent.py",
        old="        start = tmod.time()\n        response = await litellm.acompletion(\n            model=self._model,\n            api_key=self._api_key,\n            api_base=self._api_base,\n            messages=[\n                {\"role\": \"system\", \"content\": system_prompt},\n                {\"role\": \"user\", \"content\": user_message},\n            ],",
        new="        start = tmod.time()\n        _msgs = [\n            {\"role\": \"system\", \"content\": system_prompt},\n            {\"role\": \"user\", \"content\": user_message},\n        ]\n        log_prompt(\"code_agent.ask_llm\", _msgs, model=self._model)  # [prompt log v1]\n        response = await litellm.acompletion(\n            model=self._model,\n            api_key=self._api_key,\n            api_base=self._api_base,\n            messages=_msgs,",
        desc="C2: _ask_llm 调用前记录提示词",
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
    ap = argparse.ArgumentParser(description="v8 模型调用提示词日志补丁 [prompt log v1]")
    ap.add_argument("--verify", action="store_true", help="只校验，不修改")
    ap.add_argument("--rollback", action="store_true", help="回滚（反向替换 + 删除新文件）")
    args = ap.parse_args()

    root = BACKEND_ROOT
    if args.rollback:
        for p in PATCHES:
            file, status = _rollback_patch(p, root)
            print("[ROLLBACK] %-28s %s" % (file, status))
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
    print("完成。请重启后端服务；提示词日志写入 <backend>/log/{call_type}_{时间}.jsonl。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
