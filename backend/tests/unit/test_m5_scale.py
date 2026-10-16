"""M5 测试：MCP/A-B/租户配额/技能库（L1）。"""

import pytest

from execution.tenant_quota import TenantQuota
from mcp.adapter import MCPServer, MCPTool, create_mcp_server
from models.ab import route_by_variant
from skills.registry import Skill, SkillRegistry


def test_mcp_register_and_call():
    s = MCPServer(enabled=True)
    s.register(MCPTool(name="echo", description="echo", fn=lambda x: x))
    assert s.has("echo")
    assert s.call("echo", x="hi") == "hi"


def test_mcp_disabled_blocks():
    s = MCPServer(enabled=False)
    s.register(MCPTool(name="x", description="", fn=lambda: 1))
    with pytest.raises(RuntimeError):
        s.call("x")


def test_ab_routing_stable():
    v = route_by_variant("u1", ["v1", "v2", "v3"])
    v2 = route_by_variant("u1", ["v1", "v2", "v3"])
    assert v == v2
    assert v in {"v1", "v2", "v3"}


def test_ab_empty_raises():
    with pytest.raises(ValueError):
        route_by_variant("u1", [])


def test_tenant_quota_rpm_limit():
    q = TenantQuota(default_rpm=2, default_tpm=1000)
    assert q.allow("t1") is True
    assert q.allow("t1") is True
    assert q.allow("t1") is False


def test_tenant_quota_tpm_limit():
    q = TenantQuota(default_rpm=100, default_tpm=10)
    assert q.allow("t1", tokens=5) is True
    assert q.allow("t1", tokens=6) is False


def test_skill_registry():
    reg = SkillRegistry()
    reg.register(Skill(name="s1", description="d", fn=lambda: 42))
    assert reg.get("s1").fn() == 42
    assert "s1" in reg.list()
