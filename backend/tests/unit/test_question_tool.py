"""G-15: durable question tool backed by session forms."""

from __future__ import annotations

import asyncio

from app.session import context as session_context
from app.session import repository as repo
from execution.tools import question_tools


def test_question_waits_for_reply(monkeypatch):
    created: list[dict] = []
    monkeypatch.setattr(
        repo,
        "create_form",
        lambda sid, fid, fields, title="": created.append({"fid": fid, "fields": fields})
        or {"id": fid, "session_id": sid, "title": title, "fields": fields},
    )
    monkeypatch.setattr(
        repo,
        "list_forms",
        lambda sid: [
            {
                "id": created[0]["fid"],
                "state": "replied",
                "response": {"answer": "yes"},
            }
        ],
    )
    monkeypatch.setattr(repo, "cancel_form", lambda sid, fid: True)
    session_context.set_session_context("ses_q", None)
    out = asyncio.run(question_tools.ask_question("Continue?", "yes, no"))
    assert out == "yes"
    assert created[0]["fields"][0]["type"] == "select"


def test_question_without_session(monkeypatch):
    session_context.set_session_context("", None)
    assert "没有活动会话" in asyncio.run(question_tools.ask_question("x"))
