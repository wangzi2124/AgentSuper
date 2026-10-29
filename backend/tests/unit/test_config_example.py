"""config.example.json 完整性测试。

保证：
- 示例文件可被 JSONC 解析（注释 / 尾逗号 / 字符串内 //）。
- 示例覆盖 ``Settings`` 的全部字段，且没有多余/已删除字段。
- 示例值与代码默认值一致（密钥类空字符串除外）。
"""

from pathlib import Path

import pytest

from config import Settings, parse_jsonc

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.json"

# 示例里有意与代码默认值不同的键：密钥/可选值用空字符串便于用户填写。
_EMPTY_OVERRIDES = {
    "llm_api_key",
    "admin_token",
    "auth_secret",
}

_DELETED_KEYS = {
    "external_path_default",
    "compress_min_tokens",
    "compress_max_llm_calls",
}


def _example() -> dict:
    return parse_jsonc(EXAMPLE.read_text(encoding="utf-8-sig"))


def test_example_is_valid_jsonc():
    raw = _example()
    assert isinstance(raw, dict)
    assert raw["llm_model"] == "deepseek/deepseek-v4-flash"


def test_example_covers_all_settings_fields():
    raw = _example()
    assert set(raw) == set(Settings.model_fields)


def test_example_has_no_deleted_keys():
    raw = _example()
    assert not (_DELETED_KEYS & set(raw))


def test_example_values_match_code_defaults():
    raw = _example()
    for name, field in Settings.model_fields.items():
        default = None if callable(field.default) else field.default
        if name in _EMPTY_OVERRIDES:
            assert raw[name] == ""
            continue
        assert raw[name] == default, name


def test_jsonc_parser_handles_comments_and_trailing_commas():
    text = """
    {
      // 行注释
      "url": "http://example.com//path", // 字符串内的 // 不能删
      /* 块注释 */
      "list": [1, 2, 3,],
      "nested": {"a": 1,},
    }
    """
    assert parse_jsonc(text) == {
        "url": "http://example.com//path",
        "list": [1, 2, 3],
        "nested": {"a": 1},
    }


def test_jsonc_parser_rejects_broken_json():
    with pytest.raises(Exception):
        parse_jsonc('{"a": }')
