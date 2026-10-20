"""L1 单元测试：并行扇出门禁 should_plan_parallel（agent-1.md §2.4）。"""

from graph.routers import route_after_plan, should_plan_parallel


def _state(**kw):
    base = {
        "task_plan": [
            {"id": "t1", "agent": "file", "desc": "A", "status": "pending"},
            {"id": "t2", "agent": "research", "desc": "B", "status": "pending"},
        ],
        "plan_tier": "parallel",
        "fanout_count": 0,
        "budget_total": 10.0,
        "token_usage": {"cost": 0.0},
        "messages": [],
    }
    base.update(kw)
    return base


def test_gate_default_off(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", False)
    assert should_plan_parallel(_state()) is False


def test_gate_all_conditions_pass(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_max_fanout_calls", 2)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.20)
    assert should_plan_parallel(_state()) is True


def test_gate_requires_parallel_tier(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.0)
    assert should_plan_parallel(_state(plan_tier="serial")) is False
    assert should_plan_parallel(_state(plan_tier="single")) is False


def test_gate_requires_two_agent_tasks(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.0)
    one = _state(task_plan=[{"id": "t1", "agent": "file", "desc": "A", "status": "pending"}])
    assert should_plan_parallel(one) is False


def test_gate_fanout_count_budget(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_max_fanout_calls", 2)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.0)
    assert should_plan_parallel(_state(fanout_count=2)) is False
    assert should_plan_parallel(_state(fanout_count=1)) is True


def test_gate_remaining_budget(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_max_fanout_calls", 5)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.50)
    # 预算充足 → 放行
    assert should_plan_parallel(_state(budget_total=10.0, token_usage={"cost": 1.0})) is True
    # 剩余预算不足 → 拒绝
    assert should_plan_parallel(_state(budget_total=1.0, token_usage={"cost": 0.8})) is False
    # 未设预算 → 保守拒绝
    assert should_plan_parallel(_state(budget_total=None)) is False


def test_route_returns_sends_when_enabled(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", True)
    monkeypatch.setattr(settings, "plan_max_fanout_calls", 2)
    monkeypatch.setattr(settings, "plan_fanout_min_budget", 0.0)
    result = route_after_plan(_state())
    assert isinstance(result, list) and len(result) == 2
    assert all(getattr(s, "node", "") == "parallel_worker" for s in result)


def test_route_serial_when_disabled(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "plan_parallel_enabled", False)
    assert route_after_plan(_state()) == "execute_subagent"
