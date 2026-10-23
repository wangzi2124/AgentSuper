"""L2 integration: real main graph + PlatformContext.switch_model context migration.

Not just gates: run a real graph turn with model A to record a fact, then run the
real `switch_model` to build the summary binding, then run the next turn with
model B and assert B receives the fact from the summary while old raw messages
are not injected again.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import app.deps as deps
from app.deps import PlatformContext
from execution.filesystem import VirtualFileSystem
from graph.main_graph import build_main_graph
from models.router import ChatResult


class _RecordingModel:
    """Record the effective model id and full messages for every call."""

    def __init__(self, name: str, calls: list[dict], answer: str):
        self.name = name
        self.tier = "fake"
        self.calls = calls
        self.answer = answer

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        from models.router import request_model

        self.calls.append(
            {
                "model": request_model(),
                "messages": [dict(m) if isinstance(m, dict) else m for m in messages],
            }
        )
        return ChatResult(content=self.answer, finish_reason="stop")


class _DispatchModel:
    """Dispatch to model A/B based on the request_model contextvar."""

    def __init__(self, models):
        self.tier = "fake"
        self.models = models

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        from models.router import request_model

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
    parts: list[str] = []
    for m in messages:
        if isinstance(m, dict):
            parts.append(str(m.get("content") or ""))
        else:
            parts.append(str(getattr(m, "content", "")))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_switch_model_preserves_fact_in_next_turn(monkeypatch, tmp_path):
    calls: list[dict] = []
    model_a = _RecordingModel("model/a", calls, "Recorded: ORION-42")
    model_b = _RecordingModel("model/b", calls, "ORION-42")

    graph = build_main_graph(
        model=_DispatchModel({"model/a": model_a, "model/b": model_b}),
        vfs=VirtualFileSystem(root=str(tmp_path)),
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
        vfs=VirtualFileSystem(root=str(tmp_path)),
        sessions=_Sessions(sess),
        metrics=SimpleNamespace(),
        approvals=SimpleNamespace(),
        skill_loader=None,
    )

    async def _turn(question: str, answer_id: str, expected: str):
        out = await ctx.run(question, session_id="ses_e2e", message_id=f"um_{answer_id}")
        assert out["final_output"] == expected
        await graph.aupdate_state(
            {"configurable": {"thread_id": "ses_e2e"}},
            {"messages": [AIMessage(content=out["final_output"], id=f"am_{answer_id}")]},
        )
        return out

    await _turn(
        "Remember: project code ORION-42, config file D:/work/app.py",
        "1",
        model_a.answer,
    )
    first = calls[-1]
    assert first["model"] == "model/a"
    assert "ORION-42" in _render(first["messages"])
    assert "[历史摘要]" not in _render(first["messages"])

    epochs: dict[str, tuple] = {}
    monkeypatch.setattr("models.catalog.read_limits", lambda _mid: {"context_length": 10_000})
    monkeypatch.setattr("cognition.compressor.estimate_tokens", lambda _text: 10_000)

    async def _compress(messages, **kwargs):
        return {
            "summary": "The project code is ORION-42.",
            "recent": [],
            "triggered": True,
            "tokens": 20_000,
        }

    monkeypatch.setattr("cognition.compressor.compress_context", _compress)
    monkeypatch.setattr(
        "app.session.repository.get_epoch",
        lambda sid: epochs.get(sid),
    )

    def _upsert(sid, baseline, baseline_seq, snapshot):
        epochs[sid] = SimpleNamespace(
            baseline=baseline,
            baseline_seq=baseline_seq,
            snapshot=dict(snapshot),
        )

    monkeypatch.setattr("app.session.repository.upsert_epoch", _upsert)
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "backend" / "app" / "deps.py"))

    switched = await ctx.switch_model("ses_e2e", "model/b")
    assert switched["switched"] is True
    assert switched["summarized"] is True
    assert sess.model == "model/b"
    binding = ctx.context_binding("ses_e2e")
    assert binding["active"] is True
    assert "ORION-42" in binding["summary"]
    assert "/work/app.py" in binding["summary"]
    assert binding["context_window"] == 10_000
    assert binding["compress_threshold"] == 6_000

    await _turn("What is the project code?", "2", model_b.answer)
    second = calls[-1]
    assert second["model"] == "model/b"
    rendered = _render(second["messages"])
    assert "[历史摘要]" in rendered
    assert "ORION-42" in rendered
    assert "Remember: project code" not in rendered

    backup_dir = tmp_path / "backend" / "data" / "model_switches"
    backups = list(backup_dir.glob("ses_e2e_*.json"))
    assert backups
    payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert payload["status"] == "committed"
    assert payload["from"] == "model/a"
    assert payload["to"] == "model/b"
    assert len(payload["messages"]) == 2
    audit = (backup_dir / "switches.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(audit)["session_id"] == "ses_e2e"
    assert json.loads(audit)["status"] == "committed"
