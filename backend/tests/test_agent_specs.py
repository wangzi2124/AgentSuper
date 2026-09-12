# -*- coding: utf-8 -*-
"""Agent 规格注册表（规则集化）用例。

验证 agent_specs 声明式规格驱动工具 allowlist（对齐 opencode agent.ts 权限规则集）：
  - build/explore/plan 三个规格齐全，字段语义正确
  - explore.tools == 只读工具集（"*": deny + 只读 allowlist 的可编程来源）
  - plan.tools == ("tool_task",) + task_subagents == ("explore",)（可委派 explore 探索）
  - build.mode == primary / tools is None / extended_timeout True
  - get_agent_spec 未知名称抛 KeyError、_or_none 返回 None

运行：pytest tests/test_agent_specs.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent.agent_specs import (
    AgentSpec, get_agent_spec, get_agent_spec_or_none, iter_agent_specs,
)
from app.agent.sub_tools import _READONLY_TOOL_NAMES, _WRITE_TOOL_NAMES


def test_specs_registered():
    names = {s.name for s in iter_agent_specs()}
    assert names == {"build", "explore", "plan"}


def test_build_spec():
    spec = get_agent_spec("build")
    assert isinstance(spec, AgentSpec)
    assert spec.mode == "primary"
    assert spec.tools is None  # 主 Agent 自身 graph 工具，不裁剪
    assert spec.extended_timeout is True


def test_explore_spec_readonly_allowlist():
    spec = get_agent_spec("explore")
    assert spec.mode == "subagent"
    assert set(spec.tools) == set(_READONLY_TOOL_NAMES)
    # 规则集驱动：写/执行工具不在 allowlist 中，schema 与运行时都拿不到
    assert set(spec.tools) & set(_WRITE_TOOL_NAMES) == set()


def test_plan_spec_delegates_explore():
    spec = get_agent_spec("plan")
    assert spec.mode == "primary"
    assert spec.tools == ("tool_task",)  # 唯一工具：委派 explore
    assert spec.task_subagents == ("explore",)
    assert spec.extended_timeout is False


def test_get_spec_unknown():
    with pytest.raises(KeyError):
        get_agent_spec("nope")
    assert get_agent_spec_or_none("nope") is None