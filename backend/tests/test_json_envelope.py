# -*- coding: utf-8 -*-
"""strip_json_envelope：剥离弱模型把整条回答包进 JSON 外壳的情况。

运行：pytest tests/test_json_envelope.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.utils.json_repair import strip_json_envelope


def test_role_content_envelope():
    assert strip_json_envelope('{"role": "assistant", "content": "你好"}') == "你好"


def test_response_envelope():
    assert strip_json_envelope('{"response": "hello"}') == "hello"


def test_single_content_envelope():
    assert strip_json_envelope('{"content": "正文"}') == "正文"


def test_nested_message_envelope():
    assert strip_json_envelope('{"message": {"role": "assistant", "content": "嵌套正文"}}') == "嵌套正文"


def test_code_fenced_envelope():
    assert strip_json_envelope('```json\n{"role":"assistant","content":"fenced"}\n```') == "fenced"


def test_plain_text_unchanged():
    assert strip_json_envelope("你好，有什么可以帮你？") == "你好，有什么可以帮你？"


def test_multifield_non_envelope_unchanged():
    # 多字段且无 role → 不当作信封，避免误伤正常 JSON 回答
    s = '{"name": "a", "value": 1}'
    assert strip_json_envelope(s) == s


def test_empty_content_unchanged():
    s = '{"role": "assistant", "content": ""}'
    assert strip_json_envelope(s) == s


def test_none_and_empty():
    assert strip_json_envelope(None) is None
    assert strip_json_envelope("") == ""


def test_empty_json_forms_normalized_to_empty():
    # 弱模型偶发返回空 JSON 对象/数组/null → 归一化为空串（交由兜底文案）
    assert strip_json_envelope("{}") == ""
    assert strip_json_envelope("[]") == ""
    assert strip_json_envelope("null") == ""
    assert strip_json_envelope("  {}  ") == ""
