# -*- coding: utf-8 -*-
"""parse_answer_envelope：弱模型 JSON 外壳的「有界清理」（只认固定文本键）。

运行：pytest tests/test_json_envelope.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.utils.json_repair import parse_answer_envelope, is_unparsed_json_answer, is_tool_call_markup


def test_response_key():
    assert parse_answer_envelope('{"response": "你好"}') == "你好"


def test_response_key_with_extra_meta():
    assert parse_answer_envelope('{"type": "response", "response": "x"}') == "x"


def test_text_key_with_extra_meta():
    # qwen2.5:3b 实测自造形态
    s = '{"type": "section_header", "text": "backend/main.py 5 行", "bg_color": "F5DEB3"}'
    assert parse_answer_envelope(s) == "backend/main.py 5 行"


def test_content_and_answer_keys():
    assert parse_answer_envelope('{"content": "c"}') == "c"
    assert parse_answer_envelope('{"answer": "a"}') == "a"


def test_code_fenced():
    assert parse_answer_envelope('```json\n{"response":"fenced"}\n```') == "fenced"


def test_empty_json_forms_normalized_to_empty():
    assert parse_answer_envelope("{}") == ""
    assert parse_answer_envelope("[]") == ""
    assert parse_answer_envelope("null") == ""
    assert parse_answer_envelope("  {}  ") == ""


def test_no_text_key_passthrough():
    s = '{"name": "a", "value": 1}'
    assert parse_answer_envelope(s) == s


def test_plain_text_passthrough():
    assert parse_answer_envelope("你好，有什么可以帮你？") == "你好，有什么可以帮你？"


def test_none_and_empty():
    assert parse_answer_envelope(None) is None
    assert parse_answer_envelope("") == ""


def test_is_unparsed_json_answer():
    # 自造 schema（无标准文本键）→ 判定为失败回答
    assert is_unparsed_json_answer('{"type": "content", "title": "x", "bullets": []}') is True
    assert is_unparsed_json_answer('{"name": "a", "value": 1}') is True
    # 普通文本 / 空 → 不是
    assert is_unparsed_json_answer("你好") is False
    assert is_unparsed_json_answer("") is False
    assert is_unparsed_json_answer(None) is False


def test_is_tool_call_markup():
    assert is_tool_call_markup('<｜DSML｜tool_calls>\n<｜DSML｜invoke name="read_file">') is True
    assert is_tool_call_markup('<|tool_calls|>\n<|call|>1</|call|>') is True
    assert is_tool_call_markup("正常回答") is False
    assert is_tool_call_markup("") is False
    assert is_tool_call_markup(None) is False
