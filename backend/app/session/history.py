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
    """一次性装载结果：模型应看到的历史消息。"""

    def __init__(self, messages: list[Message], epoch: Optional[ContextEpoch]):
        self.messages = messages
        self.epoch = epoch


def load(session_id: str) -> HistoryLoad:
    """装载模型视角历史（对齐 SessionHistory.load 的过滤逻辑）。

    - epoch.baseline_seq：上下文纪元建立时消息日志水位（跳过其前的消息）
    - 压缩后：仅保留 compaction seq 起的消息
    """
    epoch = repository.get_epoch(session_id)
    compaction_seq = repository.latest_compaction_seq(session_id)

    after_seq = 0
    if compaction_seq is not None:
        after_seq = max(after_seq, compaction_seq)
    if epoch is not None:
        after_seq = max(after_seq, epoch.baseline_seq)

    messages = repository.list_messages(session_id, after_seq=after_seq)
    return HistoryLoad(messages=messages, epoch=epoch)


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
