"""app.agent.supervisor 拆分 facade —— 代码已拆入 `supermod/`（constants, SupervisorAgent），
本模块保持原有 import 路径（含下划线私有符号）与 __all__。"""
# 由 split_module.py 生成，勿手工改动此文件头
from .supermod.constants import *
from .supermod.base import SupervisorAgentBase
from .supermod.core import SupervisorAgentCore
from .supermod.decompose import SupervisorAgentDecompose
from .supermod.parallel import SupervisorAgent
import logging
logger = logging.getLogger(__name__)

__all__ = ["DECOMPOSE_SYSTEM_PROMPT", "SUB_RESULT_TRUNC", "SYNTHESIS_SYSTEM_PROMPT", "SupervisorAgent", "logger"]
