"""子 Agent 定义与执行（对齐 agent.md §5.4 / §3.1）。

每个 worker 是「主 Agent 的一个工具」：
- 独立系统提示词（prompts/system/*.md）
- 固定模型 tier（§2.2）
- 工具白名单（最小权限）
- 独立上下文（只拿结构化任务，不继承主对话）

主 Agent 通过注册的 `task(agent, instruction, context)` 工具委派；
并行扇出由 `graph/nodes/parallel.py` 用 `asyncio.gather` 实现。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from execution.tool_registry import ToolRegistry
from models.router import get_model
from prompts.loader import PromptLoader

from .events import push_event
from .nodes.base import run_tool_loop

logger = logging.getLogger(__name__)

SUBTASK_SCOPE = "location"


@dataclass
class SubAgent:
    """子 Agent 规格。"""

    name: str
    prompt_path: str
    tier: str = "medium"
    tools: tuple[str, ...] = ()
    max_rounds: int = 6
    description: str = ""

    def system_prompt(self) -> str:
        try:
            return PromptLoader().load(self.prompt_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cannot load prompt %s: %s", self.prompt_path, exc)
            return f"你是 {self.name} 子 Agent，请完成分配的子任务。"


SUBAGENTS: dict[str, SubAgent] = {
    "research": SubAgent(
        name="research",
        prompt_path="system/research_agent.md",
        tier="medium",
        tools=("read_file", "ls", "grep"),
        description="信息检索与文档分析专家（只读工具）。",
    ),
    "code": SubAgent(
        name="code",
        prompt_path="system/code_agent.md",
        tier="complex",
        tools=("read_file", "write_file", "edit_file", "ls", "grep", "shell_exec"),
        description="代码编写、调试与沙箱执行专家。",
    ),
    "file": SubAgent(
        name="file",
        prompt_path="system/file_agent.md",
        tier="simple",
        tools=("read_file", "write_file", "edit_file", "ls", "grep"),
        description="工作区文件读写/编辑/检索专家。",
    ),
}


async def run_subagent(
    spec: SubAgent,
    task: str,
    *,
    registry: ToolRegistry,
    context: str = "",
    model: Any | None = None,
) -> dict[str, Any]:
    """执行一个子 Agent，返回 {agent, text, rounds, usage}。

    子 Agent 使用工具白名单（最小权限），只接收结构化任务与可选上下文，
    不继承主 Agent 的对话历史（§5.4 上下文隔离）。
    """
    model = model or get_model(spec.tier)
    scoped = registry.subset(spec.tools) if spec.tools else registry

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": spec.system_prompt()},
    ]
    if context:
        messages.append(
            {"role": "system", "content": f"<task-context>\n{context}\n</task-context>"}
        )
    messages.append({"role": "user", "content": task})

    push_event(
        {
            "type": "agent_start",
            "agent_id": spec.name,
            "agent_name": spec.name,
        }
    )
    try:
        result = await run_tool_loop(
            model,
            messages,
            scoped,
            scope=SUBTASK_SCOPE,
            max_rounds=spec.max_rounds,
            agent_id=spec.name,
        )
    except Exception as exc:
        push_event(
            {
                "type": "agent_end",
                "agent_id": spec.name,
                "agent_name": spec.name,
                "status": "failed",
                "error": str(exc),
            }
        )
        raise
    push_event(
        {
            "type": "agent_end",
            "agent_id": spec.name,
            "agent_name": spec.name,
            "status": "completed",
            "content": result.get("text", ""),
        }
    )
    return {
        "agent": spec.name,
        "text": result["text"],
        "rounds": result["rounds"],
        "usage": result["usage"],
    }


def make_runner(
    registry: ToolRegistry,
    *,
    model_factory: Callable[[SubAgent], Any] | None = None,
) -> Callable[..., Any]:
    """构造 `runner(agent, task, context="") -> dict`，供 task 工具与并行节点复用。"""

    def _factory(spec: SubAgent) -> Any:
        return model_factory(spec) if model_factory is not None else get_model(spec.tier)

    async def runner(agent: str, task: str, context: str = "") -> dict[str, Any]:
        spec = SUBAGENTS.get(agent)
        if spec is None:
            return {
                "agent": agent,
                "text": f"错误：未知子 Agent '{agent}'（可用：{', '.join(SUBAGENTS)}）",
                "rounds": 0,
                "usage": {},
            }
        return await run_subagent(
            spec, task, registry=registry, context=context, model=_factory(spec)
        )

    return runner


def make_task_tool(runner: Callable[..., Any]) -> Callable[..., Any]:
    """把子 Agent runner 暴露为主 Agent 可调用的 `task` 工具。"""

    async def task(agent: str, instruction: str, context: str = "") -> str:
        """把子任务委派给专职子 Agent。

        Args:
            agent: 子 Agent 名称，可选 research / code / file。
            instruction: 交给子 Agent 的完整任务指令（自包含）。
            context: 可选背景信息（如已知文件路径、先前结论）。
        """
        result = await runner(agent, instruction, context)
        return result.get("text", "")

    return task
