"""拆分模块 `decompose`（含 SupervisorAgentDecompose 与最终的 SupervisorAgent）。

原文件 docstring: Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 按意图关键词决定路由到哪个顶层子 Agent（build / plan）
  3. 通过 AgentBus 转发请求并等待回复
  4. 将子 Agent 的回答包装后返回给用户

[C4 · 2026-09-29] 本模块同时是继承链的**末级切片**（原第 4 级 `parallel.py`
已删除 —— 它的 `_execute_parallel` / `_synthesize` 因 `_decompose` 恒返回单个
子任务而永不可达）。`_llm_decompose` / `_validate_subtasks` 与
`DECOMPOSE_SYSTEM_PROMPT` 随之删除：supervisor 层不做 fan-out，
并行由主 Agent 的 `tool_task` 委派链承担（对齐 opencode）。

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future
"""
# ── 复制自原模块的顶层 import ──

import logging

import re

from .core import SupervisorAgentCore

logger = logging.getLogger(__name__)


# ── 类分块（verbatim，继承链切片）──
class SupervisorAgentDecompose(SupervisorAgentCore):
    def _is_greeting(self, q: str) -> bool:
        """[B6] 判断是否为简短的寒暄/闲聊（用于 ≤24 字符快速路径）。"""
        return any(k in q for k in self._GREETING_KEYWORDS)

    async def _decompose(self, question: str) -> list[dict]:
        """将请求路由到合适的 Agent。

        [opencode build 合并] rag/code/web_search 已合并为单一 build agent
        （知识库 + 代码/文件 + 内建 web 搜索），故不再按 kb/code/web 关键词拆分；
        顶层只有 build/plan 两个命令（对齐 opencode）：明确的"规划/出方案"意图走 plan，
        其余统一由 build 处理（探索代码库等由 build 的 tool_task 委派 explore 子 Agent）。
        返回格式: [{"agent": "build" | "plan", "question": "..."}]（恒为单元素）

        [C4] 本方法恒返回**单个**子任务 —— 顶层不做并行分解（原 `_llm_decompose`
        扇出路径已删除）。要多部分并行请让 build/plan 经 `tool_task` 委派子 Agent。
        """
        q = question.strip().lower()

        # ── 快速路径: 关键词 + 可用 Agent 判断 ──
        available_agents = [a for a in self._bus.list_agents() if a in self.ROUTABLE_AGENTS]

        # 明确的"规划/出方案"意图 → plan（对齐 opencode plan_enter：复杂任务先规划再执行）。
        plan_keywords = [
            "先规划", "先计划", "做计划", "制定计划", "修改计划", "生成计划", "计划一下",
            "实施计划", "实施方案", "设计方案", "设计一个方案", "规划方案", "方案设计", "拿出方案",
            "给出方案", "出一个方案", "给个方案", "给一个方案", "做一个方案", "做方案", "写方案",
            "写个计划", "创建计划", "任务拆解", "拆解任务", "开发计划", "需要规划",
            "implementation plan", "provide a plan", "plan for", "design doc",
        ]
        needs_plan = (
            any(kw in q for kw in plan_keywords) or bool(re.search(r"\bplan\b|\bplanning\b", q))
        ) and "plan" in available_agents

        if needs_plan:
            return [{"agent": "plan", "question": question}]

        # ── [B6] 简短寒暄直接走 build 免 LLM ──
        if len(q) <= 24 and self._is_greeting(q):
            return [{"agent": "build", "question": question}]

        # ── 其余情况：build 全能力覆盖，直接路由，不再逐请求 LLM 拆分 ──
        return [{"agent": "build", "question": question}]


class SupervisorAgent(SupervisorAgentDecompose):
    """SupervisorAgent —— 继承链末级（原 `parallel` 切片，现已无并行职责）。

    [C4] 保留此类是为了维持「facade 从 `.supermod` 导出唯一 SupervisorAgent」的
    契约与既有导入路径 `from app.agent.supermod.decompose import SupervisorAgent`。
    """


__all__ = ['SupervisorAgentDecompose', 'SupervisorAgent']
