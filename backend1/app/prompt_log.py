# -*- coding: utf-8 -*-
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
_BASE = Path(__file__).resolve().parents[1] / "logs"  # <backend>/log


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
                f.write(line + "\n")
    except Exception:
        pass
