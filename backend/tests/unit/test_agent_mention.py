"""Chat ``@general`` / ``@explore`` mentions route to isolated subtasks."""

from __future__ import annotations

from app.deps import PlatformContext


class _Ctx:
    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    def session_workspace(self, session_id: str) -> str:
        return self.workspace or ""


def _parse(question: str, workspace: str | None = None) -> tuple[str, str]:
    return PlatformContext._parse_agent_mention(_Ctx(workspace), question, "s1")


def test_parse_leading_mention_strips_token():
    agent, text = _parse("@general 帮我总结这段代码")
    assert agent == "general"
    assert text == "帮我总结这段代码"


def test_parse_inline_mention_is_case_insensitive():
    agent, text = _parse("请 @Explore 看看这个文件")
    assert agent == "explore"
    assert text == "请 看看这个文件"


def test_parse_multiple_mentions_keeps_first_agent():
    agent, text = _parse("@general @explore 看看")
    assert agent == "general"
    assert text == "看看"


def test_parse_ignores_unknown_and_internal_agents():
    assert _parse("@title 生成标题") == ("", "@title 生成标题")
    assert _parse("@nope hi") == ("", "@nope hi")


def test_parse_ignores_email_like_tokens():
    assert _parse("mail foo@general.com") == ("", "mail foo@general.com")


def test_parse_leaves_reference_tokens_untouched():
    assert _parse("@ref:docs/spec.md 解释一下") == ("", "@ref:docs/spec.md 解释一下")


def test_parse_plain_text_is_unchanged():
    assert _parse("没有提mention") == ("", "没有提mention")
