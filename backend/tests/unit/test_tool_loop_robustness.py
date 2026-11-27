"""L1 单元测试：run_tool_loop 答案质量兜底（graph/nodes/base.py）。

真实故障：小模型把工具调用写成正文 JSON（无原生 tool_calls）→ 循环误判为
最终答案，前端看到裸 `{"id":"call_x","type":"function",...}`；或 8 轮烧尽
返回占位文案 "(达到最大工具轮数，未产出最终答案)" 直接当答案。

覆盖：
- 工具调用 JSON 文本化 → 纠正提示（nudge）后拿到纯文本答案。
- 轮数耗尽（每轮都吐 JSON）→ 一次无工具纯文本收尾调用给出最终答案。
- 连收尾调用也吐 JSON → 占位文案兜底（最后防线）。
- 普通纯文本答案首轮直接返回（不回归）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.nodes.base import MAX_TOOL_ARG_REPAIR_RETRIES, MAX_TOOL_ROUNDS, run_tool_loop
from models.router import ChatResult, ToolCall

_TOOL_JSON = json.dumps(
    {
        "id": "call_9a45f0",
        "type": "function",
        "function": {"name": "noop", "arguments": "{}"},
    },
    ensure_ascii=False,
)
# deepseek-r1:1.5b 实测的非标准形状（function_name/argument_name，无 id/type）
_TOOL_JSON_NONSTANDARD = json.dumps(
    {
        "项目名称": "列表工具",
        "function": {"function_name": "noop", "arguments": {"argument_name": "路径"}},
    },
    ensure_ascii=False,
)
# markdown 围栏包裹（弱模型常见）
_TOOL_JSON_FENCED = f"```json\n{_TOOL_JSON}\n```"
# 简短散文包裹（前后一句提示语）
_TOOL_JSON_PROSE = f"我来调用工具：{_TOOL_JSON}"
# 裸形状（无 id/type/function 包装）
_TOOL_JSON_BARE = json.dumps(
    {"name": "noop", "arguments": {"path": "."}}, ensure_ascii=False
)
# Hermes / DeepSeek DSML 工具标记
_TOOL_JSON_DSML = '<｜DSML｜tool_calls><｜DSML｜invoke name="noop">'
# 弱模型把最终回答包进 JSON 外壳
_ENVELOPE_ANSWER = json.dumps({"response": "pong"}, ensure_ascii=False)


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolDefinition(name="noop", description="占位工具", fn=lambda: "ok"))
    return reg


class _JsonThenAnswerModel:
    """第 1 轮把工具调用写成正文 JSON；第 2 轮给出正常答案。"""

    def __init__(self, first_content: str = _TOOL_JSON) -> None:
        self.tier = "fake"
        self.first_content = first_content
        self.seen: list[dict] = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.seen.append({"tools": tools, "last": dict(messages[-1])})
        if len(self.seen) == 1:
            return ChatResult(content=self.first_content, finish_reason="stop")
        return ChatResult(content="pong", finish_reason="stop")


async def test_tool_call_json_as_content_is_corrected():
    model = _JsonThenAnswerModel()
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 2
    # 第 2 轮收到的应是纠正提示（user 角色），而非重复的工具 JSON
    nudge = model.seen[1]["last"]
    assert nudge["role"] == "user"
    assert "不要输出 JSON" in nudge["content"]


async def test_nonstandard_tool_call_json_corrected():
    """deepseek-r1:1.5b 的 function_name 自造形状也要被识别并纠正。"""
    model = _JsonThenAnswerModel(first_content=_TOOL_JSON_NONSTANDARD)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 2
    nudge = model.seen[1]["last"]
    assert nudge["role"] == "user" and "不要输出 JSON" in nudge["content"]


async def test_fenced_tool_json_corrected():
    """```json 围栏包裹的工具调用要被识别并纠正。"""
    model = _JsonThenAnswerModel(first_content=_TOOL_JSON_FENCED)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 2
    assert "不要输出 JSON" in model.seen[1]["last"]["content"]


async def test_prose_wrapped_tool_json_corrected():
    """散文包裹（第一句提示 + JSON 主体）要被跨度提取后识别。"""
    model = _JsonThenAnswerModel(first_content=_TOOL_JSON_PROSE)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 2


async def test_bare_name_arguments_shape_corrected():
    """裸形状 {"name": ..., "arguments": ...}（无包装层）要被识别。"""
    model = _JsonThenAnswerModel(first_content=_TOOL_JSON_BARE)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 2


async def test_dsml_markup_corrected():
    """Hermes / DeepSeek DSML 工具标记要被识别并纠正。"""
    model = _JsonThenAnswerModel(first_content=_TOOL_JSON_DSML)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 2


async def test_answer_envelope_unwrapped():
    """弱模型把回答包进 {"response": "..."} → 解包为纯文本返回。"""
    model = _JsonThenAnswerModel(first_content=_ENVELOPE_ANSWER)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "reply pong"}], _registry()
    )
    assert out["text"] == "pong"
    assert out["rounds"] == 1  # 不是工具调用 → 首轮直接返回（解包后）


async def test_plain_json_without_tool_shape_not_nudged():
    """普通 JSON 内容（无工具形态）不触发纠正，原样返回。"""
    plain_json = json.dumps({"model": "qwen", "params": 7}, ensure_ascii=False)
    model = _JsonThenAnswerModel(first_content=plain_json)
    out = await run_tool_loop(
        model, [{"role": "user", "content": "give json"}], _registry()
    )
    assert out["text"] == plain_json
    assert out["rounds"] == 1


class _BadArgsModel:
    """第 1 轮带原生 tool_calls，但 arguments 是坏 JSON（单引号 + 尾逗号）。"""

    def __init__(self) -> None:
        self.tier = "fake"
        self.round = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="probe", arguments="{'path': 'x',}")
                ],
                finish_reason="tool-calls",
            )
        return ChatResult(content="done", finish_reason="stop")


async def test_malformed_tool_args_repaired():
    """坏 JSON 参数（单引号/尾逗号）经 utils.json_repair 修复后正确派发。"""
    captured: dict = {}

    def _probe(**kw):
        captured.update(kw)
        return "ok"

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="probe", description="探测工具", fn=_probe))
    out = await run_tool_loop(
        _BadArgsModel(), [{"role": "user", "content": "go"}], reg
    )
    assert captured == {"path": "x"}
    assert out["text"] == "done"
    assert out["rounds"] == 2


class _AlwaysJsonModel:
    """每轮都输出工具调用 JSON；仅无工具的收尾调用给纯文本。"""

    def __init__(self) -> None:
        self.tier = "fake"
        self.final_calls: list = []
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= MAX_TOOL_ROUNDS:
            return ChatResult(content=_TOOL_JSON, finish_reason="stop")
        self.final_calls.append(tools)
        return ChatResult(content="兜底后的最终答案", finish_reason="stop")


async def test_exhausted_rounds_get_plain_final_answer():
    model = _AlwaysJsonModel()
    out = await run_tool_loop(
        model, [{"role": "user", "content": "go"}], _registry()
    )
    assert out["text"] == "兜底后的最终答案"
    assert out["rounds"] == MAX_TOOL_ROUNDS + 1
    assert model.final_calls == [None]  # 恰好一次无工具收尾调用


class _AlwaysJsonNoFinalModel:
    """连收尾调用（无工具）也吐工具调用 JSON → 占位文案兜底。"""

    def __init__(self) -> None:
        self.tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content=_TOOL_JSON, finish_reason="stop")


async def test_placeholder_when_even_final_answer_is_json():
    model = _AlwaysJsonNoFinalModel()
    out = await run_tool_loop(
        model, [{"role": "user", "content": "go"}], _registry()
    )
    assert out["text"] == "(达到最大工具轮数，未产出最终答案)"
    assert out["rounds"] == MAX_TOOL_ROUNDS + 1


class _PlainAnswerModel:
    def __init__(self) -> None:
        self.tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(content="直接答案", finish_reason="stop")


async def test_plain_answer_returns_first_round():
    out = await run_tool_loop(
        _PlainAnswerModel(), [{"role": "user", "content": "hi"}], _registry()
    )
    assert out["text"] == "直接答案"
    assert out["rounds"] == 1


class _EmptyThenAnswerModel:
    """第 1 轮空回复（无 tool_calls、无正文）；被要求补答后给出正常答案。"""

    def __init__(self) -> None:
        self.tier = "fake"
        self.seen: list[dict] = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.seen.append({"tools": tools, "last": dict(messages[-1])})
        if len(self.seen) == 1:
            return ChatResult(content="", finish_reason="stop")
        return ChatResult(content="补答后的答案", finish_reason="stop")


async def test_empty_answer_nudged_and_recovered():
    """空回复不能被当答案返回 → 要求补答后拿到非空文本。"""
    model = _EmptyThenAnswerModel()
    out = await run_tool_loop(
        model, [{"role": "user", "content": "写个文件"}], _registry()
    )
    assert out["text"] == "补答后的答案"
    assert out["rounds"] == 2
    nudge = model.seen[1]["last"]
    assert nudge["role"] == "user" and "空的" in nudge["content"]


class _ToolThenAlwaysEmptyModel:
    """第 1 轮调工具（返回错误），之后每轮都空回复（连收尾调用也空）。"""

    def __init__(self, tool_name: str = "failing") -> None:
        self.tier = "fake"
        self.round = 0
        self.tool_name = tool_name

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.round += 1
        if self.round == 1:
            return ChatResult(
                content="",
                tool_calls=[ToolCall(id="c1", name=self.tool_name, arguments="{}")],
                finish_reason="tool-calls",
            )
        return ChatResult(content="", finish_reason="stop")


def _failing_registry() -> ToolRegistry:
    reg = ToolRegistry()

    def failing(**kw):
        return "错误：路径越界: C:\\tool\\hello.txt"

    reg.register(ToolDefinition(name="failing", description="报错工具", fn=failing))
    return reg


async def test_empty_answer_never_returns_empty_string():
    """最坏情况：模型始终空回复 → 绝不能返回空串，用工具轨迹摘要兜底。"""
    out = await run_tool_loop(
        _ToolThenAlwaysEmptyModel(), [{"role": "user", "content": "go"}], _failing_registry()
    )
    assert out["text"].strip() != ""
    assert "failing" in out["text"]  # 摘要里带有出错工具轨迹


async def test_empty_answer_all_success_falls_back_to_model_empty_notice():
    """轨迹全部成功但模型始终空回复 → 明确提示模型未产出内容。"""
    out = await run_tool_loop(
        _ToolThenAlwaysEmptyModel(tool_name="noop"),
        [{"role": "user", "content": "go"}],
        _registry(),
    )
    assert "模型未产出可展示内容" in out["text"]


def _capture_registry(captured: dict, parameters: dict | None = None) -> ToolRegistry:
    def write(**kwargs):
        captured.update(kwargs)
        return "written"

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="probe",
            description="捕获参数",
            fn=write,
            parameters=parameters or {},
        )
    )
    return reg


class _BadArgsThenGoodModel:
    def __init__(self) -> None:
        self.tier = "fake"
        self.calls = 0
        self.seen: list[dict] = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        self.seen.append(dict(messages[-1]))
        if self.calls == 1:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c_bad",
                        name="probe",
                        arguments="not json",
                    )
                ],
                finish_reason="tool-calls",
            )
        if self.calls == 2:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c_good",
                        name="probe",
                        arguments='{"path": "a.txt", "content": "ok"}',
                    )
                ],
                finish_reason="tool-calls",
            )
        return ChatResult(content="done", finish_reason="stop")


async def test_bad_tool_arguments_are_nudged_once_and_retried():
    model = _BadArgsThenGoodModel()
    captured: dict = {}
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _capture_registry(captured),
    )

    assert captured == {"path": "a.txt", "content": "ok"}
    assert out["text"] == "done"
    assert out["rounds"] == 3
    assert "argument_error" in out["tool_trace"][0]
    assert "argument_error" not in out["tool_trace"][1]
    nudge = model.seen[1]
    assert nudge["role"] == "user"
    assert "参数不是合法 JSON" in nudge["content"]


class _AlwaysBadArgsModel:
    def __init__(self) -> None:
        self.tier = "fake"
        self.calls = 0

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= MAX_TOOL_ARG_REPAIR_RETRIES + 1:
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"c_{self.calls}",
                        name="probe",
                        arguments="not json",
                    )
                ],
                finish_reason="tool-calls",
            )
        return ChatResult(content="done", finish_reason="stop")


async def test_bad_tool_arguments_retries_are_bounded():
    model = _AlwaysBadArgsModel()
    captured: dict = {}
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _capture_registry(captured),
    )

    assert captured == {}
    assert model.calls == MAX_TOOL_ARG_REPAIR_RETRIES + 2
    assert len(out["tool_trace"]) == MAX_TOOL_ARG_REPAIR_RETRIES + 1
    assert all("argument_error" in trace for trace in out["tool_trace"])
    assert out["text"] == "done"


class _ScriptedNativeModel:
    def __init__(self, script: list[ChatResult]) -> None:
        self.tier = "fake"
        self.script = list(script)
        self.seen: list[dict] = []

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.seen.append(dict(messages[-1]))
        return self.script.pop(0)


def _native_call(arguments: str, call_id: str = "c1") -> ChatResult:
    return ChatResult(
        content="",
        tool_calls=[ToolCall(id=call_id, name="probe", arguments=arguments)],
        finish_reason="tool-calls",
    )


async def test_schema_repairs_stringified_arguments_before_call():
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "count": {"type": "integer"},
            "ok": {"type": "boolean"},
            "tags": {"type": "array"},
        },
        "required": ["path"],
    }
    model = _ScriptedNativeModel(
        [
            _native_call(
                json.dumps(
                    {"path": "a.txt", "count": "2", "ok": "true", "tags": '["x"]'},
                    ensure_ascii=False,
                )
            ),
            ChatResult(content="done", finish_reason="stop"),
        ]
    )
    captured: dict = {}
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _capture_registry(captured, schema),
    )

    assert captured == {"path": "a.txt", "count": 2, "ok": True, "tags": ["x"]}
    assert "argument_error" not in out["tool_trace"][0]
    assert out["text"] == "done"


async def test_closed_schema_unknown_field_triggers_retry():
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    }
    model = _ScriptedNativeModel(
        [
            _native_call('{"path": "a.txt", "extra": 1}'),
            _native_call('{"path": "a.txt"}', call_id="c2"),
            ChatResult(content="done", finish_reason="stop"),
        ]
    )
    captured: dict = {}
    out = await run_tool_loop(
        model,
        [{"role": "user", "content": "go"}],
        _capture_registry(captured, schema),
    )

    assert captured == {"path": "a.txt"}
    assert "未知字段" in out["tool_trace"][0]["argument_error"]
    assert "未知字段" in model.seen[1]["content"]
    assert out["text"] == "done"


class _ContentPolicyThenAnswerAdapter:
    mode = "native"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        if len(self.calls) == 1:
            raise RuntimeError(
                'litellm.BadRequestError: DeepseekException - {"error":{"message":'
                '"Content Exists Risk (request_id: a5ffe4fc)"}}'
            )
        return ChatResult(content="新闻摘要", finish_reason="stop")


async def test_content_policy_retry_sanitizes_tool_output():
    adapter = _ContentPolicyThenAnswerAdapter()
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "最新有什么新闻"},
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "德语媒体：深圳峰会——独裁者的狂欢 黄仁勋复职，特朗普向习近平学到了什么？",
        },
    ]

    out = await run_tool_loop(
        SimpleNamespace(tier="fake"), messages, ToolRegistry(), adapter=adapter
    )

    assert out["text"] == "新闻摘要"
    assert len(adapter.calls) == 2
    retried_tool_content = adapter.calls[1][-1]["content"]
    assert "独裁者" not in retried_tool_content
    assert "[已过滤]" in retried_tool_content
    assert "习近平" in retried_tool_content


