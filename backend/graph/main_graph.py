"""主图编排（对齐 agent.md §3.2）。

supervisor → plan / rag / memory_recall / context_compress → execute_subagent
→ supervisor（回环，含硬闸）→ synthesize → memory_store → END
"""

from __future__ import annotations

import logging
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
    make_filter_node,
    make_grade_node,
    make_memory_recall_node,
    make_memory_store_node,
    make_parallel_worker_node,
    make_plan_node,
    make_rag_node,
    make_rewrite_node,
    make_supervisor_node,
    make_synthesize_node,
)
from .routers import route_after_grade, route_after_plan, route_after_supervisor
from .state import AgentState
from .subagent import SUBAGENTS, make_runner, make_task_tool

logger = logging.getLogger(__name__)

DEFAULT_TOOL_SCOPE = "location"


def _default_execute_prompt() -> str:
    try:
        return PromptLoader().load("system/file_agent.md")
    except Exception:  # noqa: BLE001
        return "你是执行专家，使用提供的工具完成任务。"


def _build_simple_grade_model() -> Any | None:
    """为 grade/rewrite 构造 simple 档位模型（不依赖压缩开关）。

    agent-2 §13.4.2 要求 grade/rewrite 用 tier-simple（二分类/改写是轻任务）。
    独立于 `compress_model_enabled`，保证「关压缩」不影响「检索门禁」。
    构造失败返回 None → grade 节点保守放行（`rag_relevant=True`），不阻塞主流程。
    """
    try:
        return get_model("simple", max_tokens=256)
    except Exception as exc:  # noqa: BLE001
        logger.warning("grade/rewrite model build failed, pass-through: %s", exc)
        return None


def default_registry(
    vfs: VirtualFileSystem | None = None,
    *,
    with_shell: bool = True,
) -> tuple[ToolRegistry, VirtualFileSystem]:
    vfs = vfs or VirtualFileSystem()
    registry = ToolRegistry()
    # 读/写文件工具都标 `require_approval=True`（§5.3）：沙箱/允许根内由审批门控
    # 自动放行（零弹窗），越界才人工审批。shell 命令同理 —— 命令白名单命中直接放行，
    # 否则弹审批（见 `PlatformContext.request_permission` 的 command 分支）。
    from execution.tools.file_tools import register_file_tools

    register_file_tools(registry, vfs, scope=DEFAULT_TOOL_SCOPE)
    # 内置联网 / 天气能力（原 http-client / internet-search / weather 插件转为内置）：
    # web_search、web_fetch 只读放行；http_request 需审批；get_weather 只读放行。
    from execution.tools.weather_tools import register_weather_tools
    from execution.tools.web_tools import register_web_tools

    register_web_tools(registry)
    register_weather_tools(registry)
    if with_shell:
        registry.register_function(
            shell_exec,
            name="shell_exec",
            description=(shell_exec.__doc__ or "").strip(),
            scope=DEFAULT_TOOL_SCOPE,
            require_approval=True,
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
    skills_catalog: str = "",
    compress_model: Any | None = None,
    compressor: Any | None = None,
    synthesize_model: Any | None = None,
    plan_model: Any | None = None,
    grade_model: Any | None = None,
    rewrite_model: Any | None = None,
    subagent_models: Any | None = None,
    enable_task_tool: bool = True,
    max_concurrency: int = 3,
) -> Any:
    """构建并编译主图（开发用 MemorySaver；生产换 PostgresSaver）。"""
    _injected_model = model is not None
    model = model or get_model("medium")
    if registry is None:
        registry, _ = default_registry(vfs)
    system_prompt = system_prompt if system_prompt is not None else _default_execute_prompt()
    # 技能清单（名称 + 描述 + 对应 load_skill_* 工具）并入 system prompt；
    # 正文按需加载，避免常驻稀释注意力。
    if skills_catalog:
        system_prompt = f"{system_prompt}\n\n{skills_catalog}"

    # 压缩模型（agent-1.md §1）：压缩=信息压缩而非推理 → simple tier，输出硬上限
    # `compress_max_tokens`（默认 800）；可用 COMPRESS_MODEL 独立别名隔离成本（§1.3）。
    # 关闭压缩模型时不注入 → compress 节点仅按规则截断（不调模型）。
    if compress_model is None:
        from config import settings

        if getattr(settings, "compress_model_enabled", True):
            try:
                from models.router import get_compress_model

                compress_model = get_compress_model(
                    max_tokens=getattr(settings, "compress_max_tokens", 800),
                )
            except Exception as exc:  # noqa: BLE001 - 构造失败降级为规则截断
                logger.warning("compress model build failed, rule-only compression: %s", exc)
                compress_model = None

    # 计划模型（agent-1.md §2）：串行分解=默认路径，用 medium tier（§2.2/§2.6）。
    # 关闭时 plan_node 恒走单任务快速路径。并行扇出另由 `plan_parallel_enabled` +
    # should_plan_parallel 门禁控制（§2.4），默认关。
    # ⚠ 仅在**未注入测试模型**时自动构造 —— 否则脚本化 FakeModel 的图会意外
    # 调真实网络模型（集成测试 test_m1_graph_flow 依赖图内无隐藏调用）。
    if plan_model is None and not _injected_model:
        from config import settings

        if getattr(settings, "plan_model_enabled", True):
            try:
                plan_model = get_model("medium")
            except Exception as exc:  # noqa: BLE001 - 构造失败降级为单任务
                logger.warning("plan model build failed, single-task only: %s", exc)
                plan_model = None

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
    # 前置闸门：调用任何模型**之前**拦掉纯语气词/问候语（固定模板直接作答）。
    builder.add_node("filter", make_filter_node())
    builder.add_node("supervisor", make_supervisor_node())
    builder.add_node("plan", make_plan_node(plan_model))
    builder.add_node("rag", make_rag_node(retrieve, reranker=reranker))
    # §13.4：检索质量门禁 + 查询改写，复用 tier-simple 档位（§13.4.2「复用 §1.3 的
    # compress-model」指的是**模型档位**，不是压缩开关）。此前二者直接取 `compress_model`，
    # 而 `compress_model_enabled=False` 时 `compress_model` 为 None → grade/rewrite 被静默
    # 关掉（RAG 质量门禁失效）。此处独立构造：优先复用 compress_model，其次自建 simple
    # 档位，保证「关压缩」不影响「检索门禁」。
    # ⚠ 与 plan_model 同样：仅在**未注入测试模型**时自动挂载 —— 否则脚本化 FakeModel
    # 的图会为每次检索偷偷调真实网络模型（集成测试不得联网）。
    if grade_model is None and not _injected_model:
        grade_model = compress_model or _build_simple_grade_model()
    if rewrite_model is None and not _injected_model:
        rewrite_model = compress_model or _build_simple_grade_model()
    builder.add_node("grade_docs", make_grade_node(grade_model))
    builder.add_node("rewrite", make_rewrite_node(rewrite_model))
    builder.add_node("memory_recall", make_memory_recall_node(store))
    builder.add_node("context_compress", make_compress_node(compress_model, compressor=compressor))
    builder.add_node(
        "execute_subagent",
        make_execute_node(model, registry, system_prompt, scope=DEFAULT_TOOL_SCOPE),
    )
    # Send 动态扇出 worker（§2.3）：route_after_plan 逐个 Send 到本节点并发执行。
    # （旧静态三路 `parallel` 节点/边已删除 —— `route_after_plan` 从不返回字符串
    # `"parallel"`，该节点是死接线；并行一律走 `Send → parallel_worker`。）
    builder.add_node("parallel_worker", make_parallel_worker_node(subagent_runner))
    builder.add_node("synthesize", make_synthesize_node(synthesize_model))
    builder.add_node("memory_store", make_memory_store_node(store))

    builder.set_entry_point("filter")
    builder.add_edge("filter", "supervisor")
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
        {"execute_subagent": "execute_subagent"},
    )
    builder.add_edge("rag", "grade_docs")
    builder.add_conditional_edges(
        "grade_docs",
        route_after_grade,
        {"generate": "execute_subagent", "rewrite": "rewrite"},
    )
    builder.add_edge("rewrite", "rag")
    builder.add_edge("memory_recall", "execute_subagent")
    builder.add_edge("execute_subagent", "supervisor")
    builder.add_edge("parallel_worker", "synthesize")
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
    budget_total: float | None = None,
    use_vector_db: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """便捷入口：传入问题，返回终态图状态（图内后台异步节点，依赖 ainvoke）。"""
    from langchain_core.messages import HumanMessage

    graph = graph or build_main_graph(**kwargs)
    state: dict[str, Any] = {
        "messages": [HumanMessage(content=question)],
        "iteration_count": 0,
        "use_vector_db": bool(use_vector_db),
        "rag_done": False,
        "rag_relevant": False,
        "rewrite_count": 0,
        "rewritten_query": "",
    }
    if budget_total is not None:
        state["budget_total"] = float(budget_total)
    return await graph.ainvoke(
        state, config={"configurable": {"thread_id": thread_id}}
    )
