# -*- coding: utf-8 -*-
"""批次 1 审计修复的回归测试（docs/code-audit-report-2026-09-29.md）。

覆盖：
- B1 `responses.fail()` 位置参数误用 → 错误被前端当成功（app/api/models.py 曾用
     `fail(str(e))`，把文案塞进 code 字段；前端 `apiRequest` 只认「数字 code」
     才是错误信封，字符串 code 会让错误响应被原样当作成功数据返回）。
- B2/B3/B4 模型 API 的 4 条路由返回明文 api_key / 触发重探测却无 `require_admin`。
- A1 `RAGAgent.refresh_tools()` 覆盖式重建 self.tools，把 tool_web_search /
     tool_task / tool_memory_* / tool_tts_* 永久丢掉（开/关一次技能即触发）。
- A5 `loader._split_frontmatter` 必须要求首行就是 `---`，否则正文里的分隔线
     会被误当 frontmatter，回写时损坏用户正文。

运行：pytest tests/test_audit_batch1_regressions.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.api.responses import fail, ok


# ── B1: fail() 的 code 必须始终是整数 ───────────────────────────────────────

def test_fail_keyword_message_is_normal():
    body = fail(message="Provider 名称不能包含「/」")
    assert body == {"code": 1, "message": "Provider 名称不能包含「/」", "data": None}
    assert isinstance(body["code"], int)


def test_fail_positional_string_is_coerced_to_numeric_code():
    """`fail(str(e))` 曾把错误文案塞进 code → 前端不认数字 code → 错误被当成功。"""
    body = fail("text or messages is required")
    assert isinstance(body["code"], int), "code 必须是整数，否则前端 apiRequest 会跳过错误判定"
    assert body["code"] != 0
    assert body["message"] == "text or messages is required"


def test_fail_positional_string_does_not_clobber_explicit_message():
    body = fail("positional 文案", message="显式文案")
    assert body["code"] == 1
    assert body["message"] == "显式文案"


def test_fail_never_returns_code_zero():
    assert fail(0, "伪造成功")["code"] == 1
    assert fail(True, "布尔 code")["code"] == 1


def test_ok_still_zero_code():
    assert ok({"x": 1}) == {"code": 0, "message": "ok", "data": {"x": 1}}


# ── B2/B3/B4: 泄露 api_key / 触发重探测的路由必须挂 require_admin ──────────

def _admin_guarded(router) -> set:
    """返回挂了 require_admin 依赖的 (method, path) 集合。"""
    from app.api.deps import require_admin

    guarded = set()
    for route in router.routes:
        for dep in getattr(route, "dependencies", []) or []:
            if getattr(dep, "dependency", None) is require_admin:
                guarded.add((tuple(sorted(route.methods)), route.path))
                break
    return guarded


def test_models_sensitive_routes_require_admin():
    """含明文 api_key 或会触发重探测的路由必须有 require_admin。"""
    from app.api import models as models_api

    guarded = _admin_guarded(models_api.router)
    paths = {p for _m, p in guarded}
    for expected in ("/models/config", "/models/export", "/models/catalog-full", "/models/reload"):
        assert expected in paths, f"{expected} 返回明文 api_key / 触发重探测，必须挂 require_admin"


def test_models_readonly_list_stays_public():
    """GET /models 供前端选择器匿名拉取目录，不含 provider 凭证，保持公开。"""
    from app.api import models as models_api

    paths = {p for _m, p in _admin_guarded(models_api.router)}
    assert "/models" not in paths


# ── A1: refresh_tools 不得丢固定工具 ───────────────────────────────────────

def _tool_names(agent):
    return {t.name for t in agent.tools}


@pytest.mark.asyncio
async def test_refresh_tools_keeps_core_tools():
    """开/关技能后，固定工具必须仍在 —— 旧实现整体覆盖 self.tools 会永久丢失。"""
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(__file__))
    from _graphmod_support import build_agent

    agent = build_agent()
    before = _tool_names(agent)
    assert "tool_web_search" in before
    assert "tool_task" in before

    await agent.refresh_tools()
    after = _tool_names(agent)
    for name in ("tool_web_search", "tool_task"):
        assert name in after, f"refresh_tools 之后 {name} 消失"


@pytest.mark.asyncio
async def test_refresh_tools_keeps_memory_and_voice_tools():
    """注入 memory / 启用的 voice 服务时，记忆与语音工具同样不能被丢掉。"""
    from app.agent.graphmod.core import RAGAgent

    agent = RAGAgent(
        SimpleNamespace(is_empty=True, invoke=lambda q, k=3: []),
        memory=SimpleNamespace(persist_path=None),
        voice_service=SimpleNamespace(enabled=True),
    )
    await agent.refresh_tools()
    names = _tool_names(agent)
    for name in ("tool_memory_set", "tool_memory_get", "tool_memory_search",
                 "tool_tts_synthesize", "tool_voice_transcribe"):
        assert name in names, f"refresh_tools 之后 {name} 消失"


@pytest.mark.asyncio
async def test_refresh_tools_preserves_registration_order():
    """注册序被技能播种/pin 逻辑依赖，重建不得改变顺序。"""
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(__file__))
    from _graphmod_support import build_agent

    agent = build_agent()
    before = [t.name for t in agent.tools]
    await agent.refresh_tools()
    assert [t.name for t in agent.tools] == before


# ── A5: frontmatter 拆分必须有首行守卫 ─────────────────────────────────────

def test_split_frontmatter_reads_leading_block():
    from app.skills.loader import _split_frontmatter

    meta, body = _split_frontmatter("---\nname: tdd\n---\n\n正文\n")
    assert meta == {"name": "tdd"}
    assert body.strip() == "正文"


def test_split_frontmatter_ignores_rule_inside_body():
    """正文里的 `---` 分隔线不得被当成 frontmatter（否则解析出垃圾 meta）。"""
    from app.skills.loader import _split_frontmatter

    raw = "介绍\n---\ntitle: 不是 frontmatter\n---\n\n后续正文"
    meta, body = _split_frontmatter(raw)
    assert meta == {}
    assert body == raw, "无 frontmatter 时必须原样返回全文，不能改写用户正文"


def test_split_frontmatter_ignores_yaml_like_body():
    """正文以 `key: value` 开头时同样不能被当成 frontmatter。"""
    from app.skills.loader import _split_frontmatter

    raw = "name: 看起来像元信息\n---\ndescription: 也在正文里\n---\n"
    meta, body = _split_frontmatter(raw)
    assert meta == {}
    assert body == raw


def test_split_frontmatter_no_frontmatter_returns_raw():
    from app.skills.loader import _split_frontmatter

    raw = "# 标题\n\n只有正文"
    assert _split_frontmatter(raw) == ({}, raw)


def test_split_frontmatter_tolerates_broken_yaml():
    from app.skills.loader import _split_frontmatter

    raw = "---\n: : :\n\tbad\n---\n正文"
    meta, body = _split_frontmatter(raw)
    assert meta == {}
    assert body == raw, "YAML 非法时应退回全文，而不是丢掉正文"


def test_split_frontmatter_non_mapping_yaml():
    from app.skills.loader import _split_frontmatter

    raw = "---\n- a\n- b\n---\n正文"
    meta, body = _split_frontmatter(raw)
    assert meta == {}
    assert body == raw


def test_get_skill_body_preserves_body_with_rule(tmp_path):
    """端到端：正文含 `---` 的技能，读正文 → 存回 → 内容不被吞掉。"""
    from app.skills.loader import SkillLoader

    root = tmp_path / "skills"
    (root / "myskill").mkdir(parents=True)
    original = "步骤一\n\n---\n\n步骤二\n"
    (root / "myskill" / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: 测试\n---\n\n" + original,
        encoding="utf-8",
    )
    loader = SkillLoader(str(root), create=True)
    loader.load_all()
    body = loader.get_skill_body("myskill")
    assert body is not None and "步骤二" in body

    loader.update_skill("myskill", content=body)
    reread = loader.get_skill_content("myskill")
    # 往返应当与规范形式逐字一致：正文里的 `---` 保留，且没有被套第二层 frontmatter。
    assert reread == (
        "---\nname: myskill\ndescription: 测试\n---\n\n步骤一\n\n---\n\n步骤二\n"
    )
