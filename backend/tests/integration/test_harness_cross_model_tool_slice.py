"""Cross-model Harness slice: A native tool call -> switch summary -> B fallback.

A's final answer deliberately omits the fact; only the tool result contains it.
B can answer only if ``state.tool_history`` survives the switch and is injected
into the next model view. The negative control clears tool_history and asserts
B cannot answer, proving the dependency.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import app.deps as deps
from app.deps import PlatformContext
from execution.filesystem import VirtualFileSystem
from execution.tool_registry import ToolDefinition, ToolRegistry
from graph.main_graph import build_main_graph
from models.router import ChatResult, ToolCall, request_model


class _ScriptedModel:
    tier = "fake"

    def __init__(self, name: str, script: list[ChatResult]):
        self.name = name
        self.script = list(script)
        self.calls: list[dict] = []

    def model_name(self) -> str:
        return self.name

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        return self.script.pop(0)


class _FactModel:
    """Fallback model: answers ORION-42 only if the fact is in its context."""

    tier = "fake"
    name = "model/b"

    def __init__(self):
        self.calls: list[dict] = []

    def model_name(self) -> str:
        return self.name

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        text = json.dumps(messages, ensure_ascii=False, default=str)
        answer = "ORION-42" if "ORION-42" in text else "unknown"
        return ChatResult(content=answer, finish_reason="stop")


class _DispatchModel:
    tier = "fake"

    def __init__(self, models):
        self.models = models

    def model_name(self) -> str:
        return request_model() or "model/a"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        model = self.models.get(request_model() or "model/a") or self.models["model/a"]
        return await model.ainvoke(messages, tools=tools, **kwargs)


class _Sessions:
    def __init__(self, sess):
        self.sess = sess
        self.persisted: list[str] = []

    def get(self, session_id):
        return self.sess

    def persist(self, session_id):
        self.persisted.append(session_id)

    def touch(self, session_id):
        self.persisted.append(session_id)


def _render(messages) -> str:
    return json.dumps(messages, ensure_ascii=False, default=str)


def _tool_call() -> ChatResult:
    return ChatResult(
        content="",
        tool_calls=[
            ToolCall(
                id="call_a",
                name="tool_read_file",
                arguments=json.dumps({"path": "secret.txt"}),
            )
        ],
        finish_reason="tool-calls",
    )


async def _run_case(tmp_path, monkeypatch, *, keep_tool_history: bool):
    (tmp_path / "secret.txt").write_text("project code ORION-42", encoding="utf-8")
    monkeypatch.delenv("HARNESS_TOOL_MODE", raising=False)
    monkeypatch.setenv("HARNESS_NATIVE_MODELS", "model/a")
    monkeypatch.setenv("HARNESS_FALLBACK_MODELS", "model/b")

    model_a = _ScriptedModel(
        "model/a",
        [
            _tool_call(),
            ChatResult(content="已完成读取。", finish_reason="stop"),
        ],
    )
    model_b = _FactModel()
    dispatch = _DispatchModel({"model/a": model_a, "model/b": model_b})

    vfs = VirtualFileSystem(root=str(tmp_path))
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="tool_read_file",
            description="read a workspace file",
            fn=lambda path: (tmp_path / path).read_text(encoding="utf-8"),
            scope="location",
        )
    )
    graph = build_main_graph(
        model=dispatch,
        registry=registry,
        vfs=vfs,
        enable_task_tool=False,
    )
    sess = SimpleNamespace(
        model="model/a",
        status="idle",
        directory=str(tmp_path),
        updated_at=0.0,
    )
    ctx = PlatformContext(
        graph=graph,
        registry=SimpleNamespace(),
        vfs=vfs,
        sessions=_Sessions(sess),
        metrics=SimpleNamespace(),
        approvals=SimpleNamespace(),
        skill_loader=None,
    )

    async def run_turn(question: str, message_id: str):
        out = await ctx.run(
            question, session_id="ses_cross", message_id=message_id
        )
        await graph.aupdate_state(
            {"configurable": {"thread_id": "ses_cross"}},
            {
                "messages": [
                    AIMessage(
                        content=out["final_output"],
                        id=f"am_{message_id}",
                    )
                ]
            },
        )
        return out

    first = await run_turn("读取 secret.txt，然后只回复完成。", "um_1")
    assert first["final_output"] == "已完成读取。"
    assert model_a.calls[0]["tools"], "A 应走原生工具调用"

    state = await ctx.state("ses_cross")
    assert state.get("tool_history"), "A 的工具历史必须写入 state"
    assert "ORION-42" in json.dumps(state["tool_history"], ensure_ascii=False)
    assert "ORION-42" not in json.dumps(
        [m for m in state.get("messages", [])], ensure_ascii=False, default=str
    )

    if not keep_tool_history:
        await graph.aupdate_state(
            {"configurable": {"thread_id": "ses_cross"}},
            {
                "tool_history": [],
                "tool_history_turn": None,
                "tool_history_anchor_id": None,
            },
        )

    epochs: dict[str, tuple] = {}
    monkeypatch.setattr(
        "models.catalog.read_limits", lambda _mid: {"context_length": 10_000}
    )
    monkeypatch.setattr(
        "cognition.compressor.estimate_tokens", lambda _text: 10_000
    )

    async def _compress(messages, **kwargs):
        return {
            "summary": "A 执行过一次文件读取，但摘要不包含具体代号。",
            "recent": [],
            "triggered": True,
            "tokens": 20_000,
        }

    monkeypatch.setattr("cognition.compressor.compress_context", _compress)
    monkeypatch.setattr(
        "app.session.repository.get_epoch", lambda sid: epochs.get(sid)
    )

    def _upsert(sid, baseline, baseline_seq, snapshot):
        epochs[sid] = SimpleNamespace(
            baseline=baseline,
            baseline_seq=baseline_seq,
            snapshot=dict(snapshot),
        )

    monkeypatch.setattr("app.session.repository.upsert_epoch", _upsert)
    monkeypatch.setattr(
        deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py")
    )

    switched = await ctx.switch_model("ses_cross", "model/b")
    assert switched["switched"] is True
    assert switched["summarized"] is True
    assert sess.model == "model/b"

    second = await run_turn("刚才读到的项目代号是什么？", "um_2")
    b_call = model_b.calls[-1]
    prepared = _render(b_call["messages"])

    assert b_call["tools"] is None, "回退模型不能收到原生 tools"
    assert '"role": "tool"' not in prepared
    assert '"tool_calls"' not in prepared
    if keep_tool_history:
        assert "ORION-42" in prepared
        assert second["final_output"] == "ORION-42"
    else:
        assert "ORION-42" not in prepared
        assert second["final_output"] == "unknown"

    audit_path = tmp_path / "backend" / "data" / "model_switches" / "switches.jsonl"
    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records[-1]["session_id"] == "ses_cross"
    assert records[-1]["status"] == "committed"


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_tool_history", [True, False])
async def test_cross_model_tool_history_slice(
    tmp_path, monkeypatch, keep_tool_history
):
    await _run_case(
        tmp_path, monkeypatch, keep_tool_history=keep_tool_history
    )
