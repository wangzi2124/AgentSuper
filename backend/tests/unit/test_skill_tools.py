"""L1 单元测试：技能接线（1+2）——`load_skill_*` 工具、技能清单、`/技能名` 强制加载。"""

from pathlib import Path

from app.skills.loader import SkillLoader
from app.skills.slash import apply_slash_skill, parse_slash_command
from app.skills.tools import make_skill_tools, skills_catalog


class _Skill:
    def __init__(self, name, description="", enabled=True, dmi=False):
        self.name = name
        self.description = description
        self.enabled = enabled
        self.disable_model_invocation = dmi


class _Loader:
    def __init__(self, skills, bodies=None):
        self._skills = {s.name: s for s in skills}
        self._bodies = bodies or {}

    def list(self):
        return list(self._skills.values())

    def get(self, name):
        return self._skills.get(name)

    def get_skill_body(self, name):
        return self._bodies.get(name)


# ── 工具 / 清单 ──────────────────────────────────────────────────────────

def test_enabled_skills_become_tools():
    loader = _Loader(
        [_Skill("novel-writing", "写小说"), _Skill("docx", "编辑文档")],
        {"novel-writing": "# 小说创作\n流程..."},
    )
    tools = make_skill_tools(loader)
    names = [t[1] for t in tools]
    assert names == ["load_skill_novel-writing", "load_skill_docx"]
    assert tools[0][0]() == "# 小说创作\n流程..."


def test_disabled_and_user_only_skills_excluded():
    loader = _Loader([
        _Skill("off", enabled=False),
        _Skill("manual-only", dmi=True),
        _Skill("ok"),
    ])
    assert [t[1] for t in make_skill_tools(loader)] == ["load_skill_ok"]


def test_catalog_lists_tool_mapping_and_skips_disabled():
    loader = _Loader([_Skill("novel-writing", "写小说"), _Skill("off", enabled=False)])
    text = skills_catalog(loader)
    assert "novel-writing" in text and "load_skill_novel-writing" in text
    assert "load_skill_off" not in text
    assert "无需再次调用工具" in text


# ── 斜杠命令 ──────────────────────────────────────────────────────────────

def test_parse_slash_command():
    assert parse_slash_command("/docx 帮我改文档") == "docx"
    assert parse_slash_command("  /novel-writing") == "novel-writing"
    assert parse_slash_command("你好") is None
    assert parse_slash_command("/etc/hosts 是什么") == "etc"


def test_slash_injects_skill_body_and_strips_prefix():
    loader = _Loader([_Skill("docx", "编辑文档")], {"docx": "# DOCX 技能\n步骤 1"})
    out, name = apply_slash_skill("/docx 把报告转成 PDF", loader)
    assert name == "docx"
    assert out.startswith("[技能指令：docx]")
    assert "# DOCX 技能" in out
    assert "把报告转成 PDF" in out
    assert "/docx" not in out


def test_slash_empty_remainder_adds_hint():
    loader = _Loader([_Skill("docx")], {"docx": "body"})
    out, name = apply_slash_skill("/docx", loader)
    assert name == "docx" and "确认必要信息" in out


def test_slash_unknown_or_disabled_leaves_message_untouched():
    loader = _Loader([_Skill("off", enabled=False)], {"off": "body"})
    for msg in ("/nope 帮我", "/off 帮我", "/etc/hosts 是什么", "普通消息"):
        out, name = apply_slash_skill(msg, loader)
        assert out == msg and name is None


def test_build_context_registers_skill_tools(tmp_path):
    """接线回归：技能必须进入主图工具表。"""
    from app.deps import build_context

    class _Model:
        tier = "fake"

        async def ainvoke(self, *_a, **_k):  # pragma: no cover
            raise AssertionError("not called during graph build")

    loader = _Loader([_Skill("docx", "编辑文档")], {"docx": "body"})
    ctx = build_context(model=_Model(), skill_loader=loader)
    assert "load_skill_docx" in ctx.registry.names()
    assert ctx.skill_loader is loader


def test_skills_load_from_disk(tmp_path):
    """真实磁盘技能源可被加载（backend/skills 是早期外部技能源）。"""
    legacy = Path(__file__).resolve().parents[2] / "skills"
    loader = SkillLoader(str(tmp_path / "skills"), create=True, extra_dirs=[str(legacy)])
    loader.load_all()
    assert loader.get("docx") is not None
