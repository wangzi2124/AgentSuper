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
from harness.history import normalize_native
from models.router import ChatResult, ToolCall
from utils.json_repair import repair_tool_args

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


def test_valid_arguments_with_inner_code_fence_pass():
    raw = json.dumps(
        {"path": "C:/x.java", "content": "```java\nclass X {}\n```"},
        ensure_ascii=False,
    )
    parsed, error = call_argument_error(ToolCall(id="1", name="write_file", arguments=raw))
    assert parsed == {"path": "C:/x.java", "content": "```java\nclass X {}\n```"}
    assert error == ""


def test_repair_tool_args_coerces_stringified_values_and_drops_optional_null():
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "ok": {"type": "boolean"},
            "tags": {"type": "array"},
            "meta": {"type": "object"},
            "limit": {"type": "integer"},
        },
        "required": ["path"],
    }
    repaired, error = repair_tool_args(
        {
            "path": "a.txt",
            "count": "2",
            "ratio": "1.5",
            "ok": "true",
            "tags": '["x"]',
            "meta": '{"a": 1}',
            "limit": None,
        },
        schema,
        tool_name="probe",
    )
    assert error == ""
    assert repaired == {
        "path": "a.txt",
        "count": 2,
        "ratio": 1.5,
        "ok": True,
        "tags": ["x"],
        "meta": {"a": 1},
    }


def test_repair_tool_args_rejects_unknown_fields_when_schema_is_closed():
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    }
    repaired, error = repair_tool_args(
        {"path": "a.txt", "extra": 1}, schema, tool_name="probe"
    )
    assert repaired == {}
    assert "未知字段" in error
    assert "extra" in error


def test_native_missing_tool_id_is_deterministic() -> None:
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a"}'},
            }
        ],
    }
    first = normalize_native([dict(message)])
    second = normalize_native([dict(message)])
    assert first == second
    call_id = first[0]["tool_calls"][0]["id"]
    assert call_id.startswith("call_")
    assert call_id == stable_call_id("read_file", '{"path": "a"}', 0)
