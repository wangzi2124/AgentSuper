"""M4 测试：配额 + 审批队列（L1）。"""

import asyncio

import pytest

from execution.approval import ApprovalQueue
from execution.tool_registry import ToolDefinition, ToolRegistry
from execution.quota import ToolRegistryQuota


def test_rate_limit_blocks_excess():
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="fast", description="", fn=lambda: "ok", scope="global", rate_limit=2))
    q = ToolRegistryQuota(reg)
    assert q.allow("fast") is True
    assert q.allow("fast") is True
    assert q.allow("fast") is False


def test_token_budget_blocks():
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="big", description="", fn=lambda: "ok", scope="global", token_budget=100))
    q = ToolRegistryQuota(reg)
    assert q.allow("big", cost_tokens=50) is True
    assert q.allow("big", cost_tokens=60) is False


@pytest.mark.asyncio
async def test_approval_queue_cycle():
    q = ApprovalQueue()
    rid = await q.request("danger", {"x": 1}, reason="test")
    req = await q.get(rid)
    assert req and req.status == "pending"
    assert await q.decide(rid, True, "ok") is True
    req = await q.get(rid)
    assert req and req.status == "approved"
    await q.cleanup()
