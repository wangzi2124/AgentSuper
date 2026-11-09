"""Direct ``/skill`` and ``@skill`` injection honours ``skill.use`` policies."""

from __future__ import annotations

from app.skills.slash import apply_slash_skill


class _Skill:
    enabled = True


class _Loader:
    def get(self, name):
        return _Skill() if name == "docx" else None

    def get_skill_body(self, name):
        return "SKILL BODY"


def _block_skill(monkeypatch, blocked_name="docx"):
    import execution.access as access

    def fake_policy_block_for(action, resource):
        if action in ("skill.use", "skill") and resource == blocked_name:
            return "Blocked by configuration policy"
        return None

    monkeypatch.setattr(access, "policy_block_for", fake_policy_block_for)


def test_slash_skill_blocked_by_policy(monkeypatch, tmp_path):
    _block_skill(monkeypatch)
    # No commands in this workspace, so /docx resolves to the skill.
    monkeypatch.setattr(
        "app.commands.load_commands", lambda workspace=None: _NoCommands()
    )
    out, name = apply_slash_skill("/docx 转 PDF", _Loader(), workspace=tmp_path)
    assert name == "docx"
    assert "配置策略禁止" in out
    assert "SKILL BODY" not in out


def test_direct_skill_blocked_by_policy(monkeypatch, tmp_path):
    _block_skill(monkeypatch)
    out, name = apply_slash_skill("@docx 转 PDF", _Loader(), workspace=tmp_path)
    assert name == "docx"
    assert "配置策略禁止" in out
    assert "SKILL BODY" not in out


def test_slash_skill_allowed_without_policy(monkeypatch, tmp_path):
    import execution.access as access

    monkeypatch.setattr(access, "policy_block_for", lambda action, resource: None)
    monkeypatch.setattr(
        "app.commands.load_commands", lambda workspace=None: _NoCommands()
    )
    out, name = apply_slash_skill("/docx 转 PDF", _Loader(), workspace=tmp_path)
    assert name == "docx"
    assert "SKILL BODY" in out


class _NoCommands:
    def get(self, name):
        return None
