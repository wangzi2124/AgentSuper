"""拆分模块 `constants`。

[C4 · 2026-09-29] 原有三个常量已全部删除（它们只服务于已删除的并行分解路径）：
  - `SUB_RESULT_TRUNC`        —— `_execute_parallel` 子结果截断，唯一消费方已删
  - `DECOMPOSE_SYSTEM_PROMPT` —— `_llm_decompose` 的分类提示词，唯一消费方已删
  - `SYNTHESIS_SYSTEM_PROMPT` —— `_synthesize` 的汇总提示词，唯一消费方已删

supervisor 不再 fan-out（顶层只有 build/plan），因此本模块**不再有任何常量**，
仅保留模块本身以维持 `from .supermod.constants import *` 的既有导入路径可用。
本文件已无实质内容，是拆分重构留下的空壳 —— 若无外部依赖可一并删除。
"""
import logging

logger = logging.getLogger(__name__)

__all__: list[str] = []
