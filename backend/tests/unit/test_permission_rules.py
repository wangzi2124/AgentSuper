"""Ordered permission rules (opencode `permissions` semantics)."""
from __future__ import annotations

from pathlib import Path

from execution.permission import PermissionManager, wildcard_match


def _mgr(tmp_path, rules):
    return PermissionManager(
        workspace=str(tmp_path),
        whitelist_path=str(tmp_path / "permissions.json"),
        rules=rules,
    )


def test_wildcard_matching_and_normalization():
    assert wildcard_match("*", "anything/at all")
    assert wildcard_match("*.env", "config/.env")
    assert wildcard_match("*.env", "C:\\proj\\.env")
    assert wildcard_match("docs/?.md", "docs/a.md")
    assert not wildcard_match("docs/?.md", "docs/ab.md")
    # shell 的 `cmd *` 也匹配不带参数的裸命令
    assert wildcard_match("git status *", "git status")
    assert wildcard_match("git status *", "git status --short")


def test_last_matching_rule_wins(tmp_path):
    mgr = _mgr(tmp_path, [
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "secrets/*", "effect": "deny"},
    ])
    assert mgr.rule_effect("read", ["secrets/token.txt"]) == "deny"
    assert mgr.rule_effect("read", ["docs/a.txt"]) == "allow"
    assert mgr.rule_effect("edit", ["docs/a.txt"]) is None


def test_path_check_honors_rules(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    mgr = _mgr(tmp_path, [
        {"action": "edit", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": "docs/*", "effect": "allow"},
    ])
    # 工作区内：规则 deny 覆盖内置 allow
    assert mgr.check(str(tmp_path / "notes.txt"), "write") == "deny"
    # 工作区内 docs/*：后面 allow 覆盖前面都 deny
    assert mgr.check(str(tmp_path / "docs" / "guide.md"), "write") == "allow"
    # 外部路径：规则 allow 免审
    mgr2 = _mgr(tmp_path, [{"action": "edit", "resource": "*", "effect": "allow"}])
    assert mgr2.check(str(outside), "write") == "allow"


def test_git_and_system_deny_cannot_be_overridden(tmp_path):
    mgr = _mgr(tmp_path, [{"action": "*", "resource": "*", "effect": "allow"}])
    assert mgr.check(str(tmp_path / ".git" / "config"), "write") == "deny"


def test_shell_rules(tmp_path):
    mgr = _mgr(tmp_path, [
        {"action": "shell", "resource": "*", "effect": "ask"},
        {"action": "shell", "resource": "git status *", "effect": "allow"},
        {"action": "shell", "resource": "rm *", "effect": "deny"},
    ])
    assert mgr.check_command("git status") == "allow"
    assert mgr.check_command("git status --short") == "allow"
    assert mgr.check_command("rm") == "deny"
    assert mgr.check_command("rm -rf x") == "deny"
    assert mgr.check_command("ls") == "ask"


def test_invalid_rules_are_dropped(tmp_path):
    mgr = _mgr(tmp_path, [
        {"action": "read", "resource": "*", "effect": "maybe"},
        {"action": "", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*", "effect": "allow"},
    ])
    assert len(mgr.rules) == 1
    assert mgr.rule_effect("read", ["x"]) == "allow"
