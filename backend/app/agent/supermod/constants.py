"""拆分模块 `constants`（含 DECOMPOSE_SYSTEM_PROMPT、SUB_RESULT_TRUNC、SYNTHESIS_SYSTEM_PROMPT）。

原文件 docstring: Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 支持任务分解：将复杂问题拆成多个子任务并行执行
  4. 通过 AgentBus 转发请求并等待回复
  5. 将子 Agent 的回答包装后返回给用户

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future"""

# ── 复制自原模块的顶层 import ──

import asyncio

import logging

import re

import time as tmod

import uuid

from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage

from app.agent.bus import AgentBus

from app.agent.memory import MemoryManager

from app.config import settings

from app.monitor import record_model_call

from app.utils.json_repair import parse_json_value

logger = logging.getLogger(__name__)

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

"""Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 支持任务分解：将复杂问题拆成多个子任务并行执行
  4. 通过 AgentBus 转发请求并等待回复
  5. 将子 Agent 的回答包装后返回给用户

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future
"""

import asyncio
import logging
import re
import time as tmod
import uuid
from typing import AsyncIterator, Optional

import litellm

from app.agent.base import BaseAgent, AgentMessage
from app.agent.bus import AgentBus
from app.agent.memory import MemoryManager
from app.config import settings
from app.monitor import record_model_call
from app.utils.json_repair import parse_json_value

logger = logging.getLogger(__name__)

# ── [token 优化 v4] 多 Agent 汇总截断：子 Agent 完整答案已直通用户，汇总只需要点 ──

SUB_RESULT_TRUNC = 3000  # 字符


# ── 分解提示词 ──

DECOMPOSE_SYSTEM_PROMPT = """你是一个路由专家。根据用户问题选择一个合适的 Agent 来处理（通常单个即可）。

当前可用的 Agent:
  - "build":    默认主 Agent（合并了知识库检索、代码/文件处理、网络搜索：可查文档/小说等
                知识库内容、可读写代码与文件、可用内置工具搜索实时信息）
  - "plan":     规划主 Agent（仅当用户明确要求"先做计划/方案"时选用，不执行操作）

要求:
1. 通常只返回一个 Agent；绝大多数问题都应路由给 "build"
2. 仅当用户明确要求输出实施计划/方案才选 "plan"
3. 每个子任务有清晰的描述

输出格式（纯 JSON 数组，不要 markdown 标记）:
[{"agent": "build", "question": "用户问题（原样或略作澄清）"}]
"""

SYNTHESIS_SYSTEM_PROMPT = """你是信息汇总专家。以下是多个并行搜索结果，请将它们整合成一个连贯、完整的回答。

要求:
- 合并信息，去除重复内容
- 按逻辑顺序（而非 Agent 顺序）组织内容
- 如果某个 Agent 返回了错误，忽略它并基于其他结果回答
- 使用中文回答
- 在回答末尾标注信息来源（如 [知识库]、[网络搜索]、[代码分析]）"""



__all__ = ["DECOMPOSE_SYSTEM_PROMPT", "SUB_RESULT_TRUNC", "SYNTHESIS_SYSTEM_PROMPT"]
