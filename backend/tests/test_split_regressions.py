# -*- coding: utf-8 -*-
"""拆分回归保护：supervisor/supermod 拆分丢失装饰器导致的运行时故障。

历史回归（由「实际聊天测试」发现）：
1. chatmod/endpoints.py 丢失 @router.post → /api/chat/multi-agent(+/stream) 404
2. supermod/base.py 丢失 @property on agent_id → bus 注册 key 变为绑定方法，
   send_and_wait target='supervisor' 报 Unknown target、消息被丢弃、SSE 挂死
3. supermod/decompose.py 丢失 @staticmethod on _validate_subtasks →
   self 被当作 data，校验恒空，多 Agent 分解静默降级为 rag
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
    from app.agent.supermod.parallel import SupervisorAgent

    agent = SupervisorAgent(AgentBus())
    bus = AgentBus()
    bus.register(agent)
    assert "supervisor" in bus.list_agents()
    assert bus.get_agent("supervisor") is agent


def test_validate_subtasks_is_staticmethod():
    """静态方法可直按类调用；若退化为实例方法，self 会抢占 data 导致恒返回 []。"""
    from app.agent.supermod.decompose import SupervisorAgentDecompose

    assert isinstance(inspect.getattr_static(SupervisorAgentDecompose, "_validate_subtasks"), staticmethod)
    assert SupervisorAgentDecompose._validate_subtasks(
        [{"agent": "rag", "question": "q"}], ["rag", "web_search", "code"]
    ) == [{"agent": "rag", "question": "q"}]
    assert SupervisorAgentDecompose._validate_subtasks("not-a-list", ["rag"]) == []