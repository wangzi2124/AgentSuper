"""G-15: durable question tool with structured recommended options."""

from __future__ import annotations

import asyncio
import types

from app.events import emitter
from app.session import context as session_context
from app.session import repository as repo
from execution.tools import question_tools


def _repo_stub(monkeypatch, state: dict) -> dict:
    created: dict = {}

    def create_form(sid, fid, fields, title=""):
        created["id"] = fid
        created["fields"] = fields
        state[fid] = {
            "id": fid, "session_id": sid, "state": "pending",
            "response": {}, "revision": 0,
        }
        return {
            "id": fid, "session_id": sid, "title": title,
            "fields": fields, "revision": 0,
        }

    def reply_form(sid, fid, response, expected_revision=None):
        if expected_revision is not None and state[fid]["revision"] != expected_revision:
            return False
        state[fid].update(
            response=response,
            state="replied",
            revision=int(state[fid].get("revision", 0)) + 1,
        )
        return True

    def cancel_form(sid, fid, expected_revision=None):
        if expected_revision is not None and state[fid]["revision"] != expected_revision:
            return False
        state[fid]["state"] = "cancelled"
        state[fid]["revision"] = int(state[fid].get("revision", 0)) + 1
        return True

    monkeypatch.setattr(repo, "create_form", create_form)
    monkeypatch.setattr(repo, "list_forms", lambda sid: list(state.values()))
    monkeypatch.setattr(repo, "reply_form", reply_form)
    monkeypatch.setattr(repo, "cancel_form", cancel_form)
    return created


def test_normalize_options_marks_recommended_and_keeps_order():
    options = question_tools.normalize_options(
        [
            {"label": "先写测试", "description": "先补契约测试"},
            {"label": "直接实现", "recommended": True, "description": "更快但有风险"},
            "先问用户 (Recommended)",
            "先写测试",
        ]
    )
    assert [item["label"] for item in options] == ["直接实现", "先问用户", "先写测试"]
    assert [item["recommended"] for item in options] == [True, True, False]
    assert options[1]["description"] == ""


def test_normalize_options_defaults_first_as_recommended():
    options = question_tools.normalize_options("保留旧代码, 重构模块")
    assert [item["label"] for item in options] == ["保留旧代码", "重构模块"]
    assert options[0]["recommended"] is True


def test_question_waits_for_reply(monkeypatch):
    state: dict = {}
    created = _repo_stub(monkeypatch, state)
    monkeypatch.setattr(
        repo,
        "list_forms",
        lambda sid: [{"id": created["id"], "state": "replied", "response": {"answer": "yes"}}],
    )
    session_context.set_session_context("ses_q", None)

    out = asyncio.run(
        question_tools.ask_question(
            "继续吗？",
            [{"label": "继续", "description": "推进实现"}],
            header="实现方向",
        )
    )

    assert out == "yes"
    field = created["fields"][0]
    assert field["type"] == "select"
    assert field["option_details"] == [
        {"label": "继续", "description": "推进实现", "recommended": True}
    ]
    assert field["multiple"] is False
    assert field["allow_custom"] is True


async def _wait_for_created_form(task: asyncio.Task, state: dict) -> str:
    for _ in range(50):
        if state:
            break
        await asyncio.sleep(0.01)
    assert state, "form was not created"
    form_id = next(iter(state))
    assert state[form_id]["state"] == "pending"
    return form_id


def test_question_wakes_immediately_on_reply(monkeypatch):
    state: dict = {}
    _repo_stub(monkeypatch, state)
    session_context.set_session_context("ses_q", None)

    async def run():
        task = asyncio.create_task(
            question_tools.ask_question("选择方向", ["A", "B"], multiple=True)
        )
        form_id = await _wait_for_created_form(task, state)
        repo.reply_form("ses_q", form_id, {"answer": ["A", "B"]})
        question_tools.wake_form(form_id)
        return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(run()) == "A, B"


def test_question_cancel_is_returned_to_model(monkeypatch):
    state: dict = {}
    _repo_stub(monkeypatch, state)
    session_context.set_session_context("ses_q", None)

    async def run():
        task = asyncio.create_task(question_tools.ask_question("选一个", ["A", "B"]))
        form_id = await _wait_for_created_form(task, state)
        repo.cancel_form("ses_q", form_id)
        question_tools.wake_form(form_id)
        return await asyncio.wait_for(task, timeout=1)

    assert "取消" in asyncio.run(run())


def test_question_timeout_emits_cancel_event(monkeypatch):
    state: dict = {}
    _repo_stub(monkeypatch, state)
    emitted: list[tuple[str, dict]] = []

    async def fake_emit(ctx, event_type, data, **kwargs):
        emitted.append((event_type, data))
        return {"type": event_type}

    monkeypatch.setattr(emitter, "emit_event", fake_emit)
    session_context.set_session_context("ses_q", types.SimpleNamespace(events=object()))

    out = asyncio.run(question_tools.ask_question("选一个", ["A", "B"], timeout_seconds=1))

    assert "超时" in out
    assert any(event_type == "form.cancelled" for event_type, _ in emitted)
    created = [data for event_type, data in emitted if event_type == "form.created"]
    assert created and created[0]["fields"][0]["option_details"]
    assert created[0]["revision"] == 0
    cancelled = [data for event_type, data in emitted if event_type == "form.cancelled"]
    assert cancelled and cancelled[0]["revision"] == 1


def test_question_without_session(monkeypatch):
    session_context.set_session_context("", None)
    assert "没有活动会话" in asyncio.run(question_tools.ask_question("x"))


def test_tool_parameters_declare_structured_options():
    schema = question_tools.QUESTION_TOOL_PARAMETERS
    assert schema["required"] == ["question", "options"]
    assert schema["properties"]["options"]["items"]["properties"]["recommended"]["type"] == "boolean"
