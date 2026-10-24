import json

from harness.adapter import (
    apply_result,
    build_fallback_prompt,
    call_argument_error,
    extract_embedded_native_calls,
    inject_fallback_prompt,
    stable_call_id,
)
from harness.delimiters import NAME_CLOSE, NAME_OPEN, render_tool_call
from models.router import ChatResult, ToolCall

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tool_read_file",
            "description": "读取工作区文件",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


def test_fallback_prompt_contains_tools_and_protocol():
    prompt = build_fallback_prompt(TOOLS)
    assert "tool_read_file" in prompt
    assert "读取工作区文件" in prompt
    assert '"path": "string"' in prompt
    assert "ꊰ▸function_name◂ꊰ" in prompt


def test_inject_into_existing_system_message():
    out = inject_fallback_prompt(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}], TOOLS
    )
    assert len(out) == 2
    assert out[0]["content"].startswith("sys")
    assert "tool_read_file" in out[0]["content"]


def test_inject_prepends_system_when_missing():
    out = inject_fallback_prompt([{"role": "user", "content": "hi"}], TOOLS)
    assert out[0]["role"] == "system"
    assert out[1]["content"] == "hi"


def test_apply_result_parses_fallback_text():
    text = "我先读文件。\n" + render_tool_call("tool_read_file", {"path": "a.txt"})
    result = apply_result(ChatResult(content=text), "fallback")
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "tool_read_file"
    assert json.loads(result.tool_calls[0].arguments) == {"path": "a.txt"}
    assert result.content == "我先读文件。"


def test_apply_result_native_embedded_json_strips_prelude():
    text = '好的，我来读取。{"name": "tool_read_file", "arguments": {"path": "b.txt"}}'
    result = apply_result(ChatResult(content=text), "native")
    assert result.tool_calls[0].name == "tool_read_file"
    assert json.loads(result.tool_calls[0].arguments) == {"path": "b.txt"}
    assert result.content == "好的，我来读取。"


def test_stable_call_id_is_deterministic_and_sequence_scoped():
    first = stable_call_id("tool_read_file", '{"path": "a.txt"}', 1)
    same = stable_call_id("tool_read_file", '{"path": "a.txt"}', 1)
    next_one = stable_call_id("tool_read_file", '{"path": "a.txt"}', 2)

    assert first == same
    assert first != next_one
    assert first.startswith("call_1_")


def test_apply_result_without_factory_uses_stable_ids():
    text = render_tool_call("tool_read_file", {"path": "a.txt"})
    first = apply_result(ChatResult(content=text), "fallback")
    second = apply_result(ChatResult(content=text), "fallback")

    assert first.tool_calls[0].id == second.tool_calls[0].id


def test_extract_embedded_list_format():
    calls, prelude = extract_embedded_native_calls(
        '[{"name": "tool_ls", "arguments": {"path": "."}}]'
    )
    assert calls[0].name == "tool_ls"
    assert prelude == ""


def test_apply_result_keeps_native_calls():
    result = apply_result(
        ChatResult(content="ok", tool_calls=[ToolCall(id="1", name="tool_ls", arguments="{}")]),
        "native",
    )
    assert result.tool_calls[0].name == "tool_ls"
    assert result.content == "ok"


def test_invalid_arguments_reported_for_repair():
    call = ToolCall(id="1", name="tool_x", arguments="{bad json")
    parsed, error = call_argument_error(call)
    assert parsed is None
    assert "不是合法 JSON" in error
    assert "tool_x" in error


def test_valid_arguments_pass():
    call = ToolCall(id="1", name="tool_x", arguments='{"a": 1}')
    parsed, error = call_argument_error(call)
    assert parsed == {"a": 1}
    assert error == ""


def test_empty_arguments_ok():
    parsed, error = call_argument_error(ToolCall(id="1", name="tool_x", arguments=""))
    assert parsed == {}
    assert error == ""
