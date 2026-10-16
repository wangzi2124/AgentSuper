"""历史与上下文装载。

对齐 opencode SessionHistory.load / context-epoch.ts：
- 读取从「压缩水位」与「上下文纪元 baseline」之后的消息，作为模型视角的历史
- 首次运行前初始化上下文纪元；压缩后 replace 重建 baseline
"""

import logging
from typing import Optional

from . import repository
from .models import ContextEpoch, Message

logger = logging.getLogger(__name__)


class HistoryLoad:
    """一次性装载结果：模型应看到的历史消息 + 每消息的 parts。"""

    def __init__(self, messages: list[Message], epoch: Optional[ContextEpoch],
                 parts: Optional[dict[str, list]] = None):
        self.messages = messages
        self.epoch = epoch
        self.parts: dict[str, list] = parts or {}


def text_from_parts(parts: list, include_reasoning: bool = False) -> str:
    """从 parts 提取模型可读文本：拼接所有 text part 的 data.text。

    include_reasoning=True 时把 assistant 的 reasoning part（data.reasoning 或 data.text）
    也拼进去，使模型视角历史能读到推理内容（对齐 opencode model history 拼接 reasoning）。
    """
    if not parts:
        return ""
    chunks = []
    for p in parts:
        ptype = p.type if hasattr(p, "type") else (p.get("type") or "")
        data = p.data if hasattr(p, "data") else (p.get("data") or {})
        if ptype == "text":
            t = data.get("text", "")
            if t:
                chunks.append(t)
        elif include_reasoning and ptype == "reasoning":
            t = data.get("reasoning") or data.get("text") or ""
            if t:
                chunks.append(f"[reasoning]\n{t}\n[/reasoning]")
    return "\n".join(chunks)


def load(session_id: str) -> HistoryLoad:
    """装载模型视角历史（对齐 SessionHistory.load 的过滤逻辑）。

    - epoch.baseline_seq：上下文纪元建立时消息日志水位（跳过其前的消息）
    - 压缩后：仅保留 compaction seq 起的消息，并把最新压缩 checkpoint
      （compaction 消息）作为 system 上下文带回，避免模型丢失摘要上下文
    - tail 回放：epoch.snapshot 携带 tail_start_id/tail_start_seq 时，把压缩时
      保留的原文轮次（水位之下、摘要之上）一并纳入模型视角，即模型看到
      [摘要 checkpoint] + [tail 原文] + [压缩后新增消息]（对齐 opencode
      preserveRecent 语义）
    """
    epoch = repository.get_epoch(session_id)
    compaction_seq = repository.latest_compaction_seq(session_id)

    after_seq = 0
    if epoch is not None:
        after_seq = max(after_seq, epoch.baseline_seq)

    # tail 回放：压缩水位之下、摘要之上的原文轮次重新进入模型视角
    tail_before_seq = None
    if epoch is not None and epoch.snapshot:
        snap = epoch.snapshot
        ts_seq = snap.get("tail_start_seq")
        ts_id = snap.get("tail_start_id")
        if isinstance(ts_seq, int):
            tail_before_seq = ts_seq
        elif ts_id:
            tail_before_seq = repository.seq_of_message(session_id, ts_id)
    if tail_before_seq is not None:
        after_seq = min(after_seq, tail_before_seq - 1)

    messages = [m for m in repository.list_messages(session_id, after_seq=after_seq)
                if m.type != "compaction"]

    # 对齐设计 §6.1：seq >= compaction.seq 且 (seq > baseline OR type != 'system')
    # → 最新 compaction 消息始终纳入模型视角（除非尚未建立）
    if compaction_seq is not None:
        checkpoint = repository.list_messages(session_id, after_seq=compaction_seq - 1)
        if checkpoint and checkpoint[0].type == "compaction":
            messages.insert(0, checkpoint[0])

    ids = [m.id for m in messages]
    parts = repository.list_parts_for_messages(ids) if ids else {}

    return HistoryLoad(messages=messages, epoch=epoch, parts=parts)


def initialize_epoch(session_id: str, baseline: str, snapshot: dict) -> ContextEpoch:
    """首次运行前初始化上下文纪元（对齐 SessionContextEpoch.initialize）。"""
    existing = repository.get_epoch(session_id)
    if existing:
        return existing
    baseline_seq = repository.latest_seq(session_id)
    repository.upsert_epoch(session_id, baseline, baseline_seq, snapshot)
    return ContextEpoch(session_id=session_id, baseline=baseline,
                        baseline_seq=baseline_seq, snapshot=snapshot)


def replace_epoch_after_compaction(session_id: str, baseline: str, snapshot: dict) -> ContextEpoch:
    """压缩后重建上下文纪元水位（对齐 SessionContextEpoch.replace）。"""
    baseline_seq = repository.latest_seq(session_id)
    repository.upsert_epoch(session_id, baseline, baseline_seq, snapshot)
    return ContextEpoch(session_id=session_id, baseline=baseline,
                        baseline_seq=baseline_seq, snapshot=snapshot)
