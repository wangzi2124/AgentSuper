from harness.delimiters import parse_tool_calls
from harness.history import normalize_fallback, normalize_history, normalize_native


def _openai_call(cid="c1", name="tool_read_file", args="{\"path\": \"a.txt\"}"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def test_native_normalizes_shapes_and_args():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "tool_x", "arguments": {"a": 1}}]},
        {"role": "tool", "tool_call_id": "", "content": "ok"},
    ]
    out = normalize_native(msgs)
    call = out[1]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["arguments"] == '{"a": 1}'
    # 没有 id 的 tool 结果无法绑定 → 文本化，避免 400
    assert out[2]["role"] == "user"
    assert "ok" in out[2]["content"]


def test_native_keeps_matching_tool_message():
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [_openai_call()]},
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
    ]
    out = normalize_native(msgs)
    assert out[1]["role"] == "tool"
    assert out[1]["name"] == "tool_read_file"


def test_native_merges_consecutive_user_messages():
    out = normalize_native(
        [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
    )
    assert len(out) == 1
    assert out[0]["content"] == "a\n\nb"


def test_fallback_textifies_calls_and_results():
    msgs = [
        {"role": "assistant", "content": "看一下", "tool_calls": [_openai_call()]},
        {"role": "tool", "tool_call_id": "c1", "content": "hello"},
    ]
    out = normalize_fallback(msgs)
    assert out[0]["role"] == "assistant"
    assert "tool_calls" not in out[0]
    assert "看一下" in out[0]["content"]
    calls, _ = parse_tool_calls(out[0]["content"])
    assert calls and calls[0].name == "tool_read_file"
    assert calls[0].parsed == {"path": "a.txt"}

    assert out[1]["role"] == "user"
    assert "tool_read_file" in out[1]["content"]
    assert "hello" in out[1]["content"]


def test_model_switch_native_history_survives_fallback():
    """模型 A（原生）产生的历史，切到模型 B（回退）后仍能读懂。"""
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "读 a.txt"},
        {"role": "assistant", "content": "", "tool_calls": [_openai_call()]},
        {"role": "tool", "tool_call_id": "c1", "content": "正文"},
    ]
    out = normalize_history(history, "fallback")
    assert all(m["role"] in ("system", "user", "assistant") for m in out)
    assert all("tool_calls" not in m for m in out)
    assert every_id_absent(out, "c1")
    calls, _ = parse_tool_calls(out[2]["content"])
    assert calls[0].name == "tool_read_file"


def test_fallback_history_native_mode_restores_valid_sequence():
    """模型 B（回退）文本化的历史，切回模型 A（原生）仍能正常发送。"""
    fallback_view = normalize_fallback(
        [
            {"role": "assistant", "content": "", "tool_calls": [_openai_call()]},
            {"role": "tool", "tool_call_id": "c1", "content": "正文"},
        ]
    )
    native_view = normalize_history(fallback_view, "native")
    assert native_view[0]["role"] == "assistant"
    assert "tool_calls" not in native_view[0]
    assert native_view[1]["role"] == "user"


def every_id_absent(messages, ident):
    return all(ident not in str(m.get("tool_call_id") or "") for m in messages)
