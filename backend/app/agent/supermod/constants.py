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

DECOMPOSE_SYSTEM_PROMPT = """你是一个任务分解专家。将用户的复杂问题拆解成多个可以并行执行的子任务。

当前可用的 Agent:
  - "rag":     知识库检索 Agent（处理文档、小说、角色、对话、知识库相关问题）
  - "web_search": 网络搜索 Agent（处理实时信息、新闻、网络资源相关问题）
  - "code":    代码 Agent（处理编程、代码编写、代码审查相关问题）

要求:
1. 每个子任务只指定一个 Agent
2. 子任务之间不要有依赖关系（可以并行执行）
3. 每个子任务有清晰的描述
4. 如果问题很简单，只需要一个 Agent，就只返回一个子任务
5. 最多拆成 3 个子任务

输出格式（纯 JSON 数组，不要 markdown 标记）:
[
  {"agent": "rag", "question": "原问题中需要知识库的部分"},
  {"agent": "web_search", "question": "原问题中需要网络搜索的部分"},
  {"agent": "code", "question": "原问题中需要代码的部分"}
]
"""

SYNTHESIS_SYSTEM_PROMPT = """你是信息汇总专家。以下是多个并行搜索结果，请将它们整合成一个连贯、完整的回答。

要求:
- 合并信息，去除重复内容
- 按逻辑顺序（而非 Agent 顺序）组织内容
- 如果某个 Agent 返回了错误，忽略它并基于其他结果回答
- 使用中文回答
- 在回答末尾标注信息来源（如 [知识库]、[网络搜索]、[代码分析]）"""



__all__ = ["DECOMPOSE_SYSTEM_PROMPT", "SUB_RESULT_TRUNC", "SYNTHESIS_SYSTEM_PROMPT"]
