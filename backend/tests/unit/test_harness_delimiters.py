from harness.delimiters import (
    ARGS_CLOSE,
    ARGS_OPEN,
    NAME_CLOSE,
    NAME_OPEN,
    parse_tool_calls,
    render_tool_call,
    render_tool_result,
)


def test_render_parse_roundtrip():
    text = render_tool_call("tool_read_file", {"path": "a.txt"})
    assert NAME_OPEN in text and NAME_CLOSE in text
    calls, cleaned = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "tool_read_file"
    assert calls[0].parsed == {"path": "a.txt"}
    assert calls[0].error == ""
    assert cleaned == ""


def test_multiple_calls_and_text_is_kept():
    text = (
        "我先看两个文件。\n"
        + render_tool_call("tool_read_file", {"path": "a.txt"})
        + "\n"
        + render_tool_call("tool_read_file", {"path": "b.txt"})
        + "\n完成后总结。"
    )
    calls, cleaned = parse_tool_calls(text)
    assert [c.name for c in calls] == ["tool_read_file", "tool_read_file"]
    assert calls[1].parsed == {"path": "b.txt"}
    assert "我先看两个文件" in cleaned
    assert "完成后总结" in cleaned
    assert NAME_OPEN not in cleaned


def test_bad_json_kept_with_error():
    text = f"{NAME_OPEN}tool_x{NAME_CLOSE}\n{ARGS_OPEN}not json{{{ARGS_CLOSE}"
    calls, cleaned = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].parsed is None
    assert calls[0].error
    assert calls[0].arguments == "not json{"
    assert cleaned == ""


def test_missing_args_block_marks_error():
    text = f"{NAME_OPEN}tool_x{NAME_CLOSE}"
    calls, _ = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].error == "缺少参数定界符"


def test_plain_text_untouched():
    calls, cleaned = parse_tool_calls("普通回答，没有工具调用。")
    assert calls == []
    assert cleaned == "普通回答，没有工具调用。"


def test_tool_result_does_not_look_like_call():
    text = render_tool_result("tool_read_file", "hello")
    calls, cleaned = parse_tool_calls(text)
    assert calls == []
    assert cleaned == text
