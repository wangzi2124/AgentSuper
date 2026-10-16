"""M5 测试：A/B 实验指标 + MCP 适配器降级 + 配额记账（L1）。"""

import pytest

from execution.quota import ToolRegistryQuota
from execution.tool_registry import ToolDefinition, ToolRegistry
from mcp.adapter import MCPServer, MCPTool, create_mcp_client, create_mcp_server
from models.ab import ABExperiment, route_by_variant


def test_ab_experiment_records_and_summarizes():
    exp = ABExperiment(name="t", variants=["v1", "v2"])
    v = exp.route("user-1")
    exp.record_result(v, success=True, latency_ms=100)
    exp.record_result(v, success=False, latency_ms=300)
    s = exp.summary()[v]
    assert s["exposures"] == 1
    assert s["successes"] == 1 and s["failures"] == 1
    assert s["success_rate"] == 0.5
    assert s["avg_latency_ms"] == 200.0


def test_ab_best_variant():
    exp = ABExperiment(name="t", variants=["v1", "v2"])
    exp.record_result("v1", success=True)
    exp.record_result("v2", success=False)
    assert exp.best_variant() == "v1"


def test_ab_best_variant_no_samples():
    assert ABExperiment(name="t", variants=["v1"]).best_variant() is None


def test_ab_empty_variants_raises():
    with pytest.raises(ValueError):
        ABExperiment(name="t", variants=[])


def test_ab_route_is_stable():
    exp = ABExperiment(name="t", variants=["a", "b", "c"])
    assert exp.route("u1") == route_by_variant("u1", ["a", "b", "c"]) == exp.route("u1")


def test_create_mcp_server_degrades_without_runtime():
    # 本环境未安装第三方 mcp SDK → 自动降级
    server = create_mcp_server(enabled=True)
    assert server.enabled is False


def test_create_mcp_client_wraps_server():
    client = create_mcp_client(enabled=False)
    assert client.list_tools() == []


def test_mcp_unregister_and_tools_listing():
    server = MCPServer(enabled=True)
    server.register(MCPTool(name="a", description="", fn=lambda: 1))
    assert [t.name for t in server.tools()] == ["a"]
    server.unregister("a")
    assert server.list() == []


def test_quota_reset_allows_again():
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="", fn=lambda: "ok", rate_limit=1))
    q = ToolRegistryQuota(reg)
    assert q.allow("t") is True
    assert q.allow("t") is False
    q.reset("t")
    assert q.allow("t") is True


def test_quota_consume_updates_budget():
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="t", description="", fn=lambda: "ok", token_budget=100))
    q = ToolRegistryQuota(reg)
    assert q.consume("t", 40) is True
    assert q.used_tokens("t") == 40
    assert q.consume("t", 80) is False
