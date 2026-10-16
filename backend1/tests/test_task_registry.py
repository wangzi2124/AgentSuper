# -*- coding: utf-8 -*-
"""TaskRegistry（opencode task_id resume + background 结果桥）用例。
"""
import asyncio
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent.graphmod.task_registry import TaskRegistry, get_task_registry, task_background_tag


@pytest.mark.asyncio
async def test_record_builds_history():
    reg = TaskRegistry()
    await reg.record("t1", "explore", "谁能解释权限模型?", "子 Agent 权限由...", "conv1")
    await reg.record("t1", "explore", "继续: 给个例子", "例子: tool_execute 需审批", "conv1")
    hist = reg.get_history("t1")
    assert [h["role"] for h in hist] == ["user", "assistant", "user", "assistant"]
    assert hist[0]["content"].startswith("谁能解释")


@pytest.mark.asyncio
async def test_record_history_cap():
    reg = TaskRegistry()
    for i in range(10):
        await reg.record("t1", "plan", f"q{i}", f"a{i}", "conv1")
    hist = reg.get_history("t1")
    assert len(hist) <= 16


async def test_get_and_peek():
    reg = TaskRegistry()
    await reg.record("t1", "explore", "q", "ans", "conv1")
    info = reg.get("t1")
    assert info["subagent_type"] == "explore"
    assert info["conversation_id"] == "conv1"
    assert reg.peek_last_answer("t1") == "ans"
    assert reg.peek_last_answer("missing") == ""
    assert reg.get("missing") is None


async def test_missing_history_is_empty():
    reg = TaskRegistry()
    assert reg.get_history("nope") == []


async def test_record_ids_by_type():
    reg = TaskRegistry()
    await reg.record("t1", "explore", "q", "a")
    await reg.record("t2", "plan", "q", "a")
    assert set(reg.record_ids()) == {"t1", "t2"}
    assert reg.record_ids("explore") == ["t1"]
    assert reg.record_ids("plan") == ["t2"]


def test_background_result_bridge():
    reg = TaskRegistry()
    reg.push_background_result("conv1", "<task id=\"b1\" state=\"completed\">...</task>")
    reg.push_background_result("conv1", "<task id=\"b2\" state=\"completed\">...</task>")
    reg.push_background_result("conv2", "<task id=\"b3\" state=\"completed\">...</task>")
    assert len(reg.drain_background_results("conv1")) == 2  # 取出即清空
    assert reg.drain_background_results("conv1") == []
    assert len(reg.drain_background_results("conv2")) == 1
    assert reg.drain_background_results("empty") == []


def test_background_result_requires_conversation_id():
    reg = TaskRegistry()
    reg.push_background_result("", "<task id=\"b1\">...</task>")
    assert reg.drain_background_results("") == []


def test_singleton_and_tag():
    assert get_task_registry() is get_task_registry()
    assert task_background_tag("b1", "conv1").startswith('<task id="b1" state="completed">')