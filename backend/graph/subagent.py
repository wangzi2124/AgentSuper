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

from execution.access import reset_agent_policy, set_agent_policy, statements_from_permission
from execution.tool_registry import ToolRegistry
from models.router import TierModel, get_model
from prompts.loader import PromptLoader, model_id_of
from agents.registry import AgentDef, AgentRegistry, load_registry

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
    prompt_body: str = ""
    model: str = ""
    permission: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)

    def system_prompt(self, model: Any | None = None) -> str:
        if self.prompt_body.strip():
            return self.prompt_body.strip()
        try:
            agent_type = self.prompt_path.rsplit("/", 1)[-1].removesuffix(".md")
            return PromptLoader().build_agent_prompt(agent_type, model_id_of(model))
        except Exception as exc:  # noqa: BLE001
            logger.warning("cannot load prompt %s: %s", self.prompt_path, exc)
            return f"你是 {self.name} 子 Agent，请完成分配的子任务。"


def _to_subagent(defn: AgentDef) -> SubAgent:
    return SubAgent(
        name=defn.name,
        prompt_path=defn.prompt_path,
        tier=defn.tier,
        tools=defn.tools,
        max_rounds=defn.max_rounds,
        description=defn.description,
        prompt_body=defn.prompt_body,
        model=defn.model,
        permission=dict(defn.permission),
        request=dict(defn.request),
    )


def _load_subagents() -> dict[str, SubAgent]:
    registry = load_registry()
    return {agent.name: _to_subagent(agent) for agent in registry.list(mode="subagent")}


SUBAGENTS: dict[str, SubAgent] = _load_subagents()


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
        {"role": "system", "content": spec.system_prompt(model)},
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
        from agents.registry import request_kwargs

        token = set_agent_policy(statements_from_permission(spec.permission))
        try:
            result = await run_tool_loop(
                model,
                messages,
                scoped,
                scope=SUBTASK_SCOPE,
                max_rounds=spec.max_rounds,
                agent_id=spec.name,
                request_params=request_kwargs(spec.request),
            )
        finally:
            reset_agent_policy(token)
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


def current_workspace() -> str | None:
    """Workspace bound to the running turn, if the platform context is present."""
    try:
        from app.session.context import current_platform_ctx, current_session_id

        session_id = current_session_id()
        ctx = current_platform_ctx()
        if not session_id or ctx is None:
            return None
        return ctx.session_workspace(session_id) or None
    except Exception:  # noqa: BLE001 - graph stays usable without the app layer
        return None


def live_agents() -> AgentRegistry:
    """Reload agent definitions from disk for the current turn's workspace."""
    return load_registry(current_workspace())


def make_runner(
    registry: ToolRegistry,
    *,
    agents: AgentRegistry | None = None,
    agents_provider: Callable[[], AgentRegistry] | None = None,
    model_factory: Callable[[SubAgent], Any] | None = None,
) -> Callable[..., Any]:
    """构造 `runner(agent, task, context="") -> dict`，供 task 工具与并行节点复用。

    ``agents_provider`` 在每次调用时现读注册表，因此通过「Agent 配置」新建或
    修改的子 Agent 无需重启后端即可生效；未提供时回退到传入的 ``agents`` 或
    启动时加载的 ``SUBAGENTS``。
    """
    agent_registry = agents

    def _factory(spec: SubAgent) -> Any:
        if model_factory is not None:
            return model_factory(spec)
        if spec.model:
            return TierModel(tier=spec.tier, model_override=spec.model)
        return get_model(spec.tier)

    def _resolve() -> AgentRegistry | None:
        if agents_provider is None:
            return agent_registry
        try:
            return agents_provider()
        except Exception:  # noqa: BLE001 - fall back to the last known registry
            logger.warning("live agent registry load failed", exc_info=True)
            return agent_registry

    async def runner(agent: str, task: str, context: str = "") -> dict[str, Any]:
        active = _resolve()
        if active is not None:
            defn = active.get(agent)
            spec = _to_subagent(defn) if defn is not None and defn.is_subagent else None
            available = ", ".join(active.subagents())
        else:
            spec = SUBAGENTS.get(agent)
            available = ", ".join(SUBAGENTS)
        if spec is None:
            return {
                "agent": agent,
                "text": f"错误：未知子 Agent '{agent}'（可用：{available}）",
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
