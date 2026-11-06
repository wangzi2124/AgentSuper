"""L1 单元测试：压缩原语（估算 / 序列化 / 模板）。"""

from cognition.compressor import (
    build_compress_prompt,
    estimate_tokens,
    serialize_messages,
)


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


def test_serialize_strips_media_payload():
    from langchain_core.messages import HumanMessage

    msg = HumanMessage(
        content=[
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 5000}},
        ]
    )
    out = serialize_messages([msg])
    assert "data:image/png" not in out
    assert "[图片]" in out


def test_build_compress_prompt_has_self_check():
    prompt = build_compress_prompt("旧摘要", "用户: 你好")
    assert "旧摘要" in prompt and "用户: 你好" in prompt
    assert "## Requirements" in prompt
    assert "## Decisions" in prompt
    assert "## Relevant Files" in prompt
    assert "## Work State" in prompt
    assert "## Important Context" in prompt
    assert "### Blocked" in prompt
