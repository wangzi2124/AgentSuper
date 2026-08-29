# -*- coding: utf-8 -*-
"""agent/tools.py 全量用例：schema 生成、技能/插件工具创建、系统提示词分支。

覆盖：_writable_workspaces、_annotation_to_json_type（Optional/List/Dict）、
_build_parameters_schema、ToolDef.to_openai_tool、create_skill_tools（描述截断/
名字净化/fn 返回内容）、create_filesystem_tools（参数 schema + 必填判定）、
create_plugin_tools、build_system_prompt_no_kb（filesystem/memory/skills/plugins/
cwd/Windows shell）。

运行：pytest tests/test_agent_tools.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.agent.tools as at


# ── _writable_workspaces ───────────────────────────────────────────────────

def test_writable_workspaces(monkeypatch):
    monkeypatch.setattr("app.permission.get_manager",
                        lambda: SimpleWs(["C:\\ws1", "C:\\ws2"]))
    assert at._writable_workspaces() == ["C:\\ws1", "C:\\ws2"]


class SimpleWs:
    def __init__(self, ws):
        self._ws = ws

    def list_workspaces(self):
        return self._ws


def test_writable_workspaces_exception(monkeypatch):
    def boom():
        raise RuntimeError
    monkeypatch.setattr("app.permission.get_manager", boom)
    assert at._writable_workspaces() == []


# ── schema 助手 ────────────────────────────────────────────────────────────

def test_annotation_to_json_type():
    assert at._annotation_to_json_type("str") == "string"
    assert at._annotation_to_json_type("<class 'int'>") == "integer"
    assert at._annotation_to_json_type("float") == "number"
    assert at._annotation_to_json_type("bool") == "boolean"
    assert at._annotation_to_json_type("dict") == "object"
    assert at._annotation_to_json_type("typing.Dict[str, int]") == "object"
    assert at._annotation_to_json_type("Mapping") == "object"
    assert at._annotation_to_json_type("typing.List[str]") == "array"
    assert at._annotation_to_json_type("tuple[int, int]") == "array"
    assert at._annotation_to_json_type("sequence") == "array"
    assert at._annotation_to_json_type("Optional[int]") == "integer"
    assert at._annotation_to_json_type("Optional[str]") == "string"
    assert at._annotation_to_json_type("any") == "string"
    assert at._annotation_to_json_type("MyCustomType") == "string"


def test_build_parameters_schema():
    params = [
        {"name": "a", "annotation": "str", "default": "x", "has_default": True},
        {"name": "b", "annotation": "int", "default": None, "has_default": False},
        {"name": "c", "annotation": "List[str]", "default": None},
    ]
    schema = at._build_parameters_schema(params)
    assert schema["properties"]["a"] == {"type": "string", "description": "Parameter a"}
    assert schema["properties"]["c"]["type"] == "array"
    assert schema["required"] == ["b", "c"]  # a 有默认不要求


def test_tooldef_to_openai_tool():
    t = at.ToolDef(name="n", description="d", parameters={"type": "object", "properties": {}}, fn=lambda: None)
    out = t.to_openai_tool()
    assert out == {
        "type": "function",
        "function": {"name": "n", "description": "d", "parameters": {"type": "object", "properties": {}}},
    }


# ── create_skill_tools ─────────────────────────────────────────────────────

class FakeSkill:
    def __init__(self, name, description):
        self.name = name
        self.description = description


class FakeSkillLoader:
    def __init__(self, skills):
        self.skills = skills

    def get_enabled_skills(self):
        return self.skills

    def get_skill_content(self, name):
        return f"CONTENT[{name}]"


def test_create_skill_tools():
    loader = FakeSkillLoader([
        FakeSkill("my-skill", "  多个  空格  的  描述 "),
        FakeSkill("docx", "d" * 250),
    ])
    tools = at.create_skill_tools(loader)
    assert len(tools) == 2
    # 名字净化：- → _，空格 → _
    assert tools[0].name == "load_skill_my_skill"
    assert tools[0].fn() == "CONTENT[my-skill]"
    # 描述压缩空格 + 截断 200 字符
    assert "多个 空格 的 描述" in tools[0].description
    assert tools[1].description.endswith("…")
    assert tools[0].parameters["required"] == []


# ── create_filesystem_tools ────────────────────────────────────────────────

def test_create_filesystem_tools():
    tools = at.create_filesystem_tools()
    names = {t.name for t in tools}
    for n in ("tool_ls", "tool_read_file", "tool_write_file", "tool_append_file",
              "tool_edit_file", "tool_glob", "tool_grep", "tool_execute",
              "tool_delete_file", "tool_rename_file", "tool_apply_patch"):
        assert n in names
    read = next(t for t in tools if t.name == "tool_read_file")
    assert read.parameters["required"] == ["path"]
    assert read.parameters["properties"]["path"]["type"] == "string"
    assert "offset" in read.parameters["properties"]
    exec_tool = next(t for t in tools if t.name == "tool_execute")
    assert exec_tool.parameters["required"] == ["command"]


# ── create_plugin_tools ────────────────────────────────────────────────────

class FakePlugin:
    def __init__(self, name, funcs, meta):
        self.name = name
        self.functions = funcs
        self.functions_meta = meta


class FakePluginLoader:
    def __init__(self, plugins):
        self.plugins = plugins

    def get_enabled_plugins(self):
        return self.plugins

    def call_function(self, pname, fname, **kwargs):
        return f"called {pname}.{fname} {kwargs}"


def test_create_plugin_tools():
    def fn_weather(city: str) -> str:
        """查询天气"""
        return city
    meta = {"weather": {"params": [{"name": "city", "annotation": "str", "default": None}],
                        "description": "查询天气工具"}}
    loader = FakePluginLoader([FakePlugin("weather", {"weather": fn_weather}, meta)])
    tools = at.create_plugin_tools(loader)
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "plugin_weather_weather"
    assert t.description == "查询天气工具"
    assert t.parameters["required"] == ["city"]
    assert t.fn(city="北京") == "called weather.weather {'city': '北京'}"
    # 缺 meta 时描述回退到 docstring
    loader2 = FakePluginLoader([FakePlugin("p2", {"f": fn_weather}, {})])
    t2 = at.create_plugin_tools(loader2)[0]
    assert "查询天气" in t2.description


# ── build_system_prompt_no_kb ──────────────────────────────────────────────

def _prompt(filesystem=True, memory=False, cwd="", skills=None, plugins=None):
    return at.build_system_prompt_no_kb(
        FakeSkillLoader(skills or []),
        FakePluginLoader(plugins or []),
        include_filesystem=filesystem,
        cwd=cwd,
        has_memory=memory,
    )


def test_prompt_basics():
    p = _prompt()
    assert "You are a helpful AI assistant." in p
    assert "tool_ls(path)" in p
    assert "tool_task(description" in p
    assert "No knowledge base available" in p
    assert "## 实施计划" in p and "## 完成情况" in p


def test_prompt_without_filesystem():
    p = _prompt(filesystem=False)
    assert "tool_ls(path)" not in p


def test_prompt_with_memory():
    p = _prompt(memory=True)
    assert "tool_memory_set" in p
    assert "Remember SPARINGLY" in p


def test_prompt_with_cwd():
    p = _prompt(cwd="/tmp/session-ws")
    assert "/tmp/session-ws  (current session working directory)" in p


def test_prompt_with_skills():
    p = _prompt(skills=[FakeSkill("docx", "文档")])
    assert "load_skill_<name>()" in p


def test_prompt_with_plugins_skips_filesystem():
    p = _prompt(plugins=[
        FakePlugin("weather", {"weather": lambda: None}, {}),
        FakePlugin("filesystem", {"tool_boom": lambda: None}, {}),
    ])
    assert "plugin_weather_weather" in p
    assert "plugin_filesystem_tool_boom" not in p  # filesystem 插件工具被跳过


def test_prompt_windows_shell_hint():
    p = _prompt()
    if sys.platform.startswith("win"):
        assert "cmd.exe" in p
    else:
        assert "cmd.exe" not in p