"""Agent 规格注册表（对齐 opencode `agent.ts` 的 Info + permission ruleset）。

opencode 里每个 Agent 由 Info 声明（name/description/mode/prompt），可用工具由
「权限规则集」在工具注册层裁剪 —— 例如 explore 的 ruleset 是 `"*": deny` + 只读
allowlist，写工具根本不会出现在 LLM 的 tools 列表里，提示词只是辅助。

这里把 build/explore/plan 三个 Agent 的规格集中声明，工具 allowlist 与运行时拒绝
都由该注册表驱动（`sub_tools.tool_loop_chat(allowlist=...)`），避免在调用点散落
`readonly` 布尔等特判。想让某个 Agent 拥有哪套工具，改这里的规则即可：

- build   : 主 Agent，走自身 graph 工具（RAGAgentWrapper），不裁剪（tools=None）。
- plan    : 规划主 Agent（primary，与 build 同为顶层命令），可经 tool_task 委派 explore
            做只读代码库探索（对齐 opencode plan-mode Phase 1），产物落盘
            <data>/plans/<conv>/plan.md，供后续 build 阶段复读执行。
- explore : 只读探索子 Agent（subagent，非顶层命令，仅作委派目标），工具 = 只读 allowlist
            （ls/read/glob/grep）。对齐 opencode：顶层只有 build/plan 两个命令，explore 由委派进入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from app.agent.sub_tools import _READONLY_TOOL_NAMES


@dataclass(frozen=True)
class AgentSpec:
    """单个 Agent 的声明式规格（对齐 opencode Info）。

    Attributes:
        name: Agent 注册名（与 AgentBus 注册 id 一致）。
        description: 一句话职责描述。
        mode: "primary"（自身 graph 工具）| "subagent"（走 sub_tools 工具循环）。
        tools: 工具 allowlist —— None = 不裁剪（主 Agent）;
               空元组 = 无工具（纯 LLM）; 元组 = 只暴露这些工具。
        extended_timeout: 是否使用扩展超时（工具密集型 Agent，默认 build）。
    """

    name: str
    description: str
    mode: str
    tools: Optional[Tuple[str, ...]]
    extended_timeout: bool = False
    task_subagents: Tuple[str, ...] = ()


_AGENT_SPECS: dict[str, AgentSpec] = {
    "build": AgentSpec(
        name="build",
        description="默认主 Agent：知识库 + 代码/文件 + 内建 web 搜索（合并原 rag/code/web_search）。",
        mode="primary",
        tools=None,
        extended_timeout=True,
    ),
    "explore": AgentSpec(
        name="explore",
        description="只读代码探索子 Agent：权限规则集 '*': deny + 只读 allowlist，写/执行工具不暴露。",
        mode="subagent",
        tools=_READONLY_TOOL_NAMES,
    ),
    "plan": AgentSpec(
        name="plan",
        description="规划主 Agent（primary）：可委派 explore 只读探索后产出结构化实施计划，产物落盘 <data>/plans/<conv>/plan.md。",
        mode="primary",
        tools=("tool_task",),
        task_subagents=("explore",),
    ),
}


def get_agent_spec(name: str) -> AgentSpec:
    """按名称取规格；未知名称抛 KeyError（承接方应先用 _or_none 判断）。"""
    return _AGENT_SPECS[name]


def get_agent_spec_or_none(name: str) -> Optional[AgentSpec]:
    """按名称取规格；未注册返回 None。"""
    return _AGENT_SPECS.get(name)


def iter_agent_specs():
    """遍历全部已声明规格（按声明顺序）。"""
    return iter(_AGENT_SPECS.values())


__all__ = [
    "AgentSpec",
    "get_agent_spec",
    "get_agent_spec_or_none",
    "iter_agent_specs",
]