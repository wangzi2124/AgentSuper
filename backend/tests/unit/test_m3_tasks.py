"""L1 单元测试：结构化任务对象（agent.md §3.4）。"""

from graph.tasks import (
    KNOWN_AGENTS,
    TaskSpec,
    is_explicit_empty_plan,
    parse_task_specs,
)


def test_roundtrip_dict():
    spec = TaskSpec(id="t1", objective="写函数", agent="code", skills=["rag"])
    d = spec.to_dict()
    assert d["agent"] == "code"
    assert TaskSpec.from_dict(d).objective == "写函数"


def test_parse_plain_json_array():
    text = '[{"id": "t1", "agent": "code", "objective": "实现排序"}]'
    specs = parse_task_specs(text)
    assert len(specs) == 1
    assert specs[0].agent == "code"
    assert specs[0].objective == "实现排序"


def test_parse_fenced_json():
    text = '说明如下：\n```json\n[{"agent": "research", "objective": "查资料"}]\n```\n以上。'
    specs = parse_task_specs(text)
    assert specs[0].agent == "research"
    assert specs[0].id == "t1"  # 自动补 id


def test_unknown_agent_falls_back_to_default():
    text = '[{"agent": "hacker", "objective": "x"}]'
    specs = parse_task_specs(text, default_agent="file")
    assert specs[0].agent == "file"


def test_invalid_json_returns_empty():
    assert parse_task_specs("这不是 JSON") == []
    assert parse_task_specs("") == []


def test_known_agents_set():
    assert set(KNOWN_AGENTS) == {"research", "code", "file"}


def test_is_explicit_empty_plan():
    # 寒暄：LLM 按 plan.md 输出空计划
    assert is_explicit_empty_plan("[]")
    assert is_explicit_empty_plan("```json\n[]\n```")
    assert is_explicit_empty_plan("输出如下：[]")
    # 解析失败 / 非空列表 → 不是显式空计划
    assert not is_explicit_empty_plan("")
    assert not is_explicit_empty_plan("这不是 JSON")
    assert not is_explicit_empty_plan('[{"agent": "code", "objective": "x"}]')
    assert not is_explicit_empty_plan("[1, 2, 3]")
