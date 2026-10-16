"""主图编排（对齐 agent.md §3.2）。

supervisor → plan / rag / memory_recall / context_compress → execute_subagent
→ supervisor（回环，含硬闸）→ synthesize → memory_store → END
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.store.memory import InMemoryStore

from execution.filesystem import VirtualFileSystem
from execution.sandbox import shell_exec
from execution.tool_registry import ToolRegistry
from models.router import get_model
from prompts.loader import PROMPTS_DIR, PromptLoader

from .nodes import (
    make_compress_node,
    make_execute_node,
    make_memory_recall_node,
    make_memory_store_node,
    make_parallel_node,
    make_plan_node,
    make_rag_node,
    make_supervisor_node,
    make_synthesize_node,
)
from .routers import route_after_plan, route_after_supervisor
from .state import AgentState
from .subagent import SUBAGENTS, make_runner, make_task_tool

DEFAULT_TOOL_SCOPE = "location"


def _default_execute_prompt() -> str:
    try:
        return PromptLoader().load("system/file_agent.md")
    except Exception:  # noqa: BLE001
        return "你是执行专家，使用提供的工具完成任务。"


def default_registry(
    vfs: VirtualFileSystem | None = None,
    *,
    with_shell: bool = True,
) -> tuple[ToolRegistry, VirtualFileSystem]:
    vfs = vfs or VirtualFileSystem()
    registry = ToolRegistry()
    # 经 `register_file_tools` 注册：写类工具（write/edit/append/delete）标记
    # `require_approval=True`（§5.3）——沙箱内路径由审批门控自动放行，越界才弹审批。
    from execution.tools.file_tools import register_file_tools

    register_file_tools(registry, vfs, scope=DEFAULT_TOOL_SCOPE)
    if with_shell:
        registry.register_function(
            shell_exec,
            name="shell_exec",
            description=(shell_exec.__doc__ or "").strip(),
            scope=DEFAULT_TOOL_SCOPE,
        )
    return registry, vfs


def build_main_graph(
    *,
    model: Any | None = None,
    registry: ToolRegistry | None = None,
    vfs: VirtualFileSystem | None = None,
    retrieve: Callable[..., list[dict[str, Any]]] | None = None,
    reranker: Any | None = None,
    store: Any | None = None,
    checkpointer: Any | None = None,
    system_prompt: str | None = None,
    compress_model: Any | None = None,
    compressor: Any | None = None,
    synthesize_model: Any | None = None,
    plan_model: Any | None = None,
    subagent_models: Any | None = None,
    enable_task_tool: bool = True,
    max_concurrency: int = 3,
) -> Any:
    """构建并编译主图（开发用 MemorySaver；生产换 PostgresSaver）。"""
    model = model or get_model("medium")
    if registry is None:
        registry, _ = default_registry(vfs)
    system_prompt = system_prompt if system_prompt is not None else _default_execute_prompt()

    # 子 Agent runner（在注册 task 工具之前捕获 base 注册表，避免自我递归）
    subagent_runner = make_runner(registry, model_factory=subagent_models)
    if enable_task_tool:
        registry.register_function(
            make_task_tool(subagent_runner),
            name="task",
            description=(
                "把子任务委派给专职子 Agent（research/code/file），返回其自包含结果。"
                f" 可用：{', '.join(SUBAGENTS)}。"
            ),
            scope="global",
        )

    builder = StateGraph(AgentState)
    builder.add_node("supervisor", make_supervisor_node())
    builder.add_node("plan", make_plan_node(plan_model))
    builder.add_node("rag", make_rag_node(retrieve, reranker=reranker))
    builder.add_node("memory_recall", make_memory_recall_node(store))
    builder.add_node("context_compress", make_compress_node(compress_model, compressor=compressor))
    builder.add_node(
        "execute_subagent",
        make_execute_node(model, registry, system_prompt, scope=DEFAULT_TOOL_SCOPE),
    )
    builder.add_node("parallel", make_parallel_node(subagent_runner, max_concurrency=max_concurrency))
    builder.add_node("synthesize", make_synthesize_node(synthesize_model))
    builder.add_node("memory_store", make_memory_store_node(store))

    builder.set_entry_point("supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "plan": "plan",
            "rag": "rag",
            "recall": "memory_recall",
            "execute": "execute_subagent",
            "compress": "context_compress",
            "synthesize": "synthesize",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "plan",
        route_after_plan,
        {"parallel": "parallel", "execute_subagent": "execute_subagent"},
    )
    builder.add_edge("rag", "execute_subagent")
    builder.add_edge("memory_recall", "execute_subagent")
    builder.add_edge("execute_subagent", "supervisor")
    builder.add_edge("parallel", "synthesize")
    builder.add_edge("context_compress", "supervisor")
    builder.add_edge("synthesize", "memory_store")
    builder.add_edge("memory_store", END)

    return builder.compile(
        checkpointer=checkpointer or MemorySaver(),
        store=store if store is not None else InMemoryStore(),
    )


async def run_agent(
    question: str,
    *,
    thread_id: str = "default",
    graph: Any | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """便捷入口：给定问题，跑完整个主图，返回最终状态（图内含异步节点，故用 ainvoke）。"""
    from langchain_core.messages import HumanMessage

    graph = graph or build_main_graph(**kwargs)
    state = {
        "messages": [HumanMessage(content=question)],
        "iteration_count": 0,
    }
    return await graph.ainvoke(
        state, config={"configurable": {"thread_id": thread_id}}
    )
