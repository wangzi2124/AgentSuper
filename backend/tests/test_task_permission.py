# -*- coding: utf-8 -*-
"""task_permission（opencode permission.task 对齐）用例：
规则求值（最后匹配者生效 / glob / 非法值跳过）、allowed_subagent_types 过滤。
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.agent.graphmod.task_permission import resolve, allowed_subagent_types


# ── resolve：顺序求值，最后匹配者生效 ───────────────────────────────────

def test_empty_rules_default_allow():
    assert resolve({}, "explore") == "allow"
    assert resolve(None, "plan") == "allow"


def test_generic_star_matches_all():
    rules = {"*": "ask"}
    assert resolve(rules, "explore") == "ask"
    assert resolve(rules, "plan") == "ask"
    assert resolve(rules, "whatever") == "ask"


def test_exact_match():
    rules = {"explore": "deny"}
    assert resolve(rules, "explore") == "deny"
    assert resolve(rules, "plan") == "allow"  # 未匹配 → 默认 allow


def test_last_match_wins():
    rules = {"*": "ask", "explore": "allow"}
    assert resolve(rules, "explore") == "allow"      # 后匹配覆盖
    assert resolve(rules, "plan") == "ask"
    rules2 = {"explore": "allow", "explore*": "deny"}
    assert resolve(rules2, "explore") == "deny"      # 后规则压过前规则


def test_glob_question_mark():
    rules = {"plan*": "deny"}
    assert resolve(rules, "plan") == "deny"
    assert resolve(rules, "planning") == "deny"
    assert resolve(rules, "explore") == "allow"


def test_case_sensitive():
    rules = {"EXPLORE": "deny"}
    assert resolve(rules, "explore") == "allow"  # fnmatchcase：大小写敏感


def test_invalid_action_ignored(caplog):
    rules = {"explore": "nuke", "plan": "deny"}
    with caplog.at_level("WARNING"):
        assert resolve(rules, "explore") == "allow"  # 非法值跳过，保持默认
        assert resolve(rules, "plan") == "deny"
    assert "Invalid task permission action" in caplog.text


def test_non_string_key_value_ignored():
    rules = {123: "deny", "explore": 42}
    assert resolve(rules, "explore") == "allow"  # 非 str 键/值均跳过


# ── allowed_subagent_types：deny 从白名单移除 ────────────────────────────

def test_allowed_subagent_types_filter_deny():
    names = ("explore", "plan")
    assert allowed_subagent_types(names, {"explore": "deny"}) == ["plan"]
    assert allowed_subagent_types(names, {"*": "deny"}) == []
    assert allowed_subagent_types(names, {"*": "ask"}) == ["explore", "plan"]
    assert allowed_subagent_types(names, {"plan*": "deny"}) == ["explore"]
    assert allowed_subagent_types(names, {}) == ["explore", "plan"]


def test_allowed_subagent_types_accept_list_and_tuple():
    assert allowed_subagent_types(["explore", "plan"], {"explore": "deny"}) == ["plan"]