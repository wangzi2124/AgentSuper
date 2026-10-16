# -*- coding: utf-8 -*-
"""拆分回归保护：supervisor/supermod 拆分丢失装饰器导致的运行时故障。

历史回归（由「实际聊天测试」发现）：
1. chatmod/endpoints.py 丢失 @router.post → /api/chat/multi-agent(+/stream) 404
2. supermod/base.py 丢失 @property on agent_id → bus 注册 key 变为绑定方法，
   send_and_wait target='supervisor' 报 Unknown target、消息被丢弃、SSE 挂死
3. [C4] 原 supermod/parallel.py 已删除（并行分解恒不可达），继承链末级切片
   迁到 supermod/decompose.py —— 本文件同步守护新的导入路径与 MRO 契约。
"""
import inspect


def test_chat_router_exposes_multi_agent_routes():
    from app.api.chat import router

    paths = {getattr(r, "path", None) for r in router.routes}
    assert "/multi-agent" in paths
    assert "/multi-agent/stream" in paths


def test_supervisor_agent_id_roundtrip_via_bus():
    """bus 注册后能以字符串 'supervisor' 解析（拆前可用，拆后曾因属性变方法而 drop）。"""
    from app.agent.bus import AgentBus
    from app.agent.supermod.decompose import SupervisorAgent

    agent = SupervisorAgent(AgentBus())
    bus = AgentBus()
    bus.register(agent)
    assert "supervisor" in bus.list_agents()
    assert bus.get_agent("supervisor") is agent


def test_supervisor_inheritance_chain_slices():
    """[C4] 继承链由 4 级（base→core→decompose→parallel）收敛为 3 级。"""
    from app.agent.supermod.base import SupervisorAgentBase
    from app.agent.supermod.core import SupervisorAgentCore
    from app.agent.supermod.decompose import SupervisorAgent, SupervisorAgentDecompose

    assert issubclass(SupervisorAgentCore, SupervisorAgentBase)
    assert issubclass(SupervisorAgentDecompose, SupervisorAgentCore)
    assert issubclass(SupervisorAgent, SupervisorAgentDecompose)
    assert SupervisorAgent.__mro__[:4] == (
        SupervisorAgent, SupervisorAgentDecompose, SupervisorAgentCore, SupervisorAgentBase,
    )


def test_facade_exports_supervisor_agent_from_decompose():
    """facade `app.agent.supervisor` 的 SupervisorAgent 与末级切片是同一个类。"""
    from app.agent import supervisor as facade
    from app.agent.supermod.decompose import SupervisorAgent

    assert facade.SupervisorAgent is SupervisorAgent


def test_parallel_decomposition_surface_removed():
    """[C4] 并行分解实现与常量已删除，不留「看起来支持并行」的死代码。"""
    from app.agent import supervisor as facade
    from app.config import Settings

    for name in ("DECOMPOSE_SYSTEM_PROMPT", "SUB_RESULT_TRUNC", "SYNTHESIS_SYSTEM_PROMPT"):
        assert not hasattr(facade, name), f"{name} 应随并行分解一并删除"
    assert not hasattr(Settings, "sub_task_fresh_history"), "SUB_TASK_FRESH_HISTORY 应已删除"

    from app.agent.supermod.decompose import SupervisorAgent as SA

    for name in ("_execute_parallel", "_synthesize", "_llm_decompose", "_validate_subtasks"):
        assert not hasattr(SA, name), f"{name} 应已删除"


def test_decompose_always_returns_single_subtask():
    """[C4] `_decompose` 恒返回 1 个子任务 —— 这正是并行分支不可达的原因。"""
    import asyncio

    from app.agent.bus import AgentBus
    from app.agent.supermod.decompose import SupervisorAgent

    class _Bus(AgentBus):
        def list_agents(self):
            return ["build", "plan", "explore"]

    sup = SupervisorAgent(_Bus())
    for q in ["今天天气怎么样", "先规划再执行", "帮我看看代码", "hi", "实现一个搜索功能"]:
        out = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            sup._decompose(q)
        )
        assert len(out) == 1, f"_decompose({q!r}) 返回了 {len(out)} 个子任务"
        assert out[0]["agent"] in ("build", "plan")
