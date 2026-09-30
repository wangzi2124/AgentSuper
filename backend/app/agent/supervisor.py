"""app.agent.supervisor 拆分 facade —— 代码已拆入 `supermod/`（SupervisorAgent），
本模块保持原有 import 路径与 __all__。

[C4 · 2026-09-29] 并行分解路径删除：`_execute_parallel` / `_synthesize` /
`_llm_decompose` / `_validate_subtasks` 及 `DECOMPOSE_SYSTEM_PROMPT` /
`SYNTHESIS_SYSTEM_PROMPT` / `SUB_RESULT_TRUNC` 一并移除 —— 它们因
`_decompose` 恒返回单个子任务而永不可达。现导出面仅剩 SupervisorAgent 与 logger。
"""
import logging

from .supermod.decompose import SupervisorAgent

logger = logging.getLogger(__name__)

__all__ = ["SupervisorAgent", "logger"]
