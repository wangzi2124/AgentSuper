"""编排层（对齐 agent.md 第 3 章）。

LangGraph StateGraph：supervisor → plan / rag / memory_recall / execute / parallel
→ synthesize → memory_store。
"""

from .main_graph import build_main_graph, default_registry, run_agent
from .parallel import build_parallel_graph, split_by_agent
from .routers import route_after_plan, route_after_supervisor
from .state import AgentState, SubAgentState
from .subagent import SUBAGENTS, SubAgent, make_runner, make_task_tool, run_subagent
from .tasks import KNOWN_AGENTS, TaskSpec, parse_task_specs

__all__ = [
    "AgentState",
    "SubAgentState",
    "KNOWN_AGENTS",
    "SUBAGENTS",
    "SubAgent",
    "TaskSpec",
    "build_main_graph",
    "build_parallel_graph",
    "default_registry",
    "make_runner",
    "make_task_tool",
    "parse_task_specs",
    "run_agent",
    "run_subagent",
    "route_after_plan",
    "route_after_supervisor",
    "split_by_agent",
]
