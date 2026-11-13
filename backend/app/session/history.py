"""上下文纪元（context epoch）水位。

模型视角的摘要 + tail 由 ``app.session.compaction.CompactionCheckpoint``
统一重建；本模块只负责建立 / 压缩后替换 epoch 水位。
"""

import logging

from . import repository
from .models import ContextEpoch

logger = logging.getLogger(__name__)


def initialize_epoch(session_id: str, baseline: str, snapshot: dict) -> ContextEpoch:
    """首次运行前初始化上下文纪元（对齐 SessionContextEpoch.initialize）。"""
    existing = repository.get_epoch(session_id)
    if existing:
        return existing
    baseline_seq = repository.latest_seq(session_id)
    repository.upsert_epoch(session_id, baseline, baseline_seq, snapshot)
    return ContextEpoch(session_id=session_id, baseline=baseline,
                        baseline_seq=baseline_seq, snapshot=snapshot)
