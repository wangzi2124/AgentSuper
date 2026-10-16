"""L1/L2 单元测试：上下文压缩（agent.md §4.3）。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cognition.compressor import (
    SELF_CHECKLIST,
    build_compress_prompt,
    compress_context,
    estimate_tokens,
    serialize_messages,
    split_by_token,
)
from models.router import ChatResult


class FakeModel:
    def __init__(self, content="结构化摘要"):
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        return ChatResult(content=self.content)


def test_estimate_tokens_cjk_weighted():
    assert estimate_tokens("你好世界") >= 6  # 4 CJK × 1.5
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") < estimate_tokens("你好世界")


def test_serialize_truncates_tool_output():
    from langchain_core.messages import ToolMessage

    long = "x" * 5000
    msg = ToolMessage(content=long, tool_call_id="t1")
    out = serialize_messages([msg])
    assert len(out) < 3000  # 2000 截断 + 前缀


def test_split_keeps_recent():
    msgs = [HumanMessage(content=f"msg-{i} " * 20) for i in range(10)]
    older, recent = split_by_token(msgs, keep_tokens=50)
    assert recent and older
    assert recent[-1] is msgs[-1]


@pytest.mark.asyncio
async def test_no_trigger_under_threshold():
    msgs = [HumanMessage(content="短")]
    out = await compress_context(msgs, threshold=10_000)
    assert out["triggered"] is False
    assert out["recent"] == msgs


@pytest.mark.asyncio
async def test_trigger_produces_summary_and_shrinks_history():
    msgs = [HumanMessage(content="内容" * 50) for _ in range(8)]
    model = FakeModel("要点摘要")
    out = await compress_context(msgs, threshold=10, keep_tokens=1, model=model)
    assert out["triggered"] is True
    assert out["summary"] == "要点摘要"
    assert model.calls == 1
    assert len(out["recent"]) == 1


@pytest.mark.asyncio
async def test_llm_failure_keeps_existing_summary():
    msgs = [HumanMessage(content="内容" * 50) for _ in range(8)]

    class Boom:
        async def ainvoke(self, *a, **k):
            raise RuntimeError("down")

    out = await compress_context(msgs, existing_summary="旧摘要", threshold=10, keep_tokens=1, model=Boom())
    assert out["summary"] == "旧摘要"
    assert out["triggered"] is True


def test_build_compress_prompt_has_self_check():
    prompt = build_compress_prompt("旧摘要", "用户: 你好")
    assert "旧摘要" in prompt and "用户: 你好" in prompt
    assert "9. 待办与风险" in prompt
    assert "压缩自检清单" in prompt
