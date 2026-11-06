"""工具参数归一化：别名映射 / 未知参数丢弃 / 标量纠正。"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution.tool_registry import ToolDefinition
from execution.tools.web_tools import make_web_tools
from graph.nodes.base import (
    _apply_path_hint,
    _current_path_hint,
    _dispatch,
    _normalize_tool_args,
    _path_anchor,
)


def _search(query: str, top_k: int = 5) -> str:
    return f"{query}:{top_k}"


def _weather(city: str, forecast_days: int = 0) -> str:
    return f"{city}:{forecast_days}"


def test_alias_maps_max_results_to_top_k():
    assert _normalize_tool_args(_search, {"query": "x", "max_results": 3}) == {
        "query": "x",
        "top_k": 3,
    }


def test_alias_maps_location_and_days_for_weather():
    assert _normalize_tool_args(
        _weather, {"location": "北京", "days": 3}
    ) == {"city": "北京", "forecast_days": 3}


def test_unknown_args_are_dropped_without_kwargs():
    assert _normalize_tool_args(_search, {"query": "x", "bogus": 1}) == {
        "query": "x"
    }


def test_alias_does_not_hijack_legit_param():
    def fn(top_k: int = 5, count: int = 0) -> str:
        return f"{top_k}:{count}"

    assert _normalize_tool_args(fn, {"count": 2}) == {"count": 2}


def test_var_keyword_tool_keeps_all_args():
    def fn(**kwargs):
        return kwargs

    args = {"anything": 1}
    assert _normalize_tool_args(fn, args) == args


def test_string_numbers_are_coerced():
    assert _normalize_tool_args(_search, {"query": "x", "top_k": "7"}) == {
        "query": "x",
        "top_k": 7,
    }


@pytest.mark.asyncio
async def test_dispatch_accepts_max_results_alias():
    tools = make_web_tools(searcher=lambda q, k: f"hit:{q}:{k}")
    td = ToolDefinition(name="web_search", description="", fn=tools["web_search"])

    out = await _dispatch(td, {"query": "退款", "max_results": 2})

    assert out == "hit:退款:2"


def test_path_hint_extracts_absolute_path_from_user_message():
    messages = [{"role": "user", "content": r"在 c:\tool 这个文件夹 有什么信息"}]

    assert _current_path_hint(messages) == r"c:\tool"


def test_path_hint_fills_missing_ls_path_but_keeps_explicit_path():
    def ls(path: str = ".") -> str:
        return path

    assert _apply_path_hint("ls", ls, {}, r"c:\tool") == {"path": r"c:\tool"}
    assert _apply_path_hint("ls", ls, {"path": "x"}, r"c:\tool") == {"path": "x"}
    assert _apply_path_hint("web_search", ls, {}, r"c:\tool") == {}


def test_path_anchor_keeps_last_absolute_directory_across_turns():
    messages = [
        {"role": "user", "content": r"在 c:\tool 路径下 有什么"},
        {"role": "assistant", "content": "已列出"},
        {"role": "user", "content": "在这个目录下 Tesseract-OCR 文件夹有什么"},
    ]

    assert _path_anchor(messages) == r"c:\tool"
    assert _current_path_hint(messages) == ""


def test_prior_dir_rebases_relative_path_but_not_absolute_path():
    def read_file(path: str) -> str:
        return path

    expected = str(Path(r"c:\tool") / "Tesseract-OCR")
    assert _apply_path_hint(
        "read_file",
        read_file,
        {"path": "Tesseract-OCR"},
        "",
        anchor=r"c:\tool",
        rebase_relative=True,
    ) == {"path": expected}
    assert _apply_path_hint(
        "read_file",
        read_file,
        {"path": r"d:\data\a.txt"},
        "",
        anchor=r"c:\tool",
        rebase_relative=True,
    ) == {"path": r"d:\data\a.txt"}


def test_write_tools_rebase_only_when_prior_dir_is_referenced():
    def write_file(path: str, content: str = "") -> str:
        return path

    expected = str(Path(r"c:\tool") / "a.txt")
    assert _apply_path_hint(
        "write_file",
        write_file,
        {"path": "a.txt", "content": "x"},
        "",
        anchor=r"c:\tool",
        rebase_relative=True,
    ) == {"path": expected, "content": "x"}
    assert _apply_path_hint(
        "write_file",
        write_file,
        {"path": "a.txt", "content": "x"},
        "",
        anchor=r"c:\tool",
        rebase_relative=False,
    ) == {"path": "a.txt", "content": "x"}
    assert _apply_path_hint(
        "write_file",
        write_file,
        {},
        "",
        anchor=r"c:\tool",
        rebase_relative=True,
    ) == {}
