from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.deps import PlatformContext
from app.session import projector, repository


def _session(tmp_path) -> str:
    sid = f"ses_proj_{uuid.uuid4().hex[:10]}"
    project = repository.resolve_project(str(tmp_path))
    repository.create_session(
        "tester",
        project.id,
        str(tmp_path),
        session_id=sid,
        title="projection",
    )
    return sid


def _messages():
    return [
        HumanMessage(content="read the file", id="um1"),
        AIMessage(
            content="",
            id="am_tool",
            tool_calls=[
                {
                    "id": "c1",
                    "name": "read_file",
                    "args": {"path": "secret.txt"},
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="ORION-42", tool_call_id="c1", name="read_file", id="tm1"),
        AIMessage(content="done", id="am_final"),
    ]


def test_projection_is_idempotent_and_keeps_tool_parts(tmp_path):
    sid = _session(tmp_path)
    first = projector.project_checkpoint_messages(sid, _messages())
    second = projector.project_checkpoint_messages(sid, _messages())

    assert first == 4
    assert second == 4
    rows = repository.list_conversation_messages(sid)
    assert [r.id for r in rows] == ["um1", "am_tool", "tm1", "am_final"]

    parts = repository.list_parts_for_messages(["am_tool"])["am_tool"]
    assert parts[0].data["tool_call_id"] == "c1"
    assert parts[0].data["state"] == "completed"
    assert parts[0].data["output"] == "ORION-42"
    assert parts[0].data["args"] == {"path": "secret.txt"}


def test_rebuild_restores_native_tool_pairing(tmp_path):
    sid = _session(tmp_path)
    projector.project_checkpoint_messages(sid, _messages())

    view = projector.rebuild_model_messages(sid)

    assert [type(m).__name__ for m in view] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "AIMessage",
    ]
    assert view[1].tool_calls[0]["id"] == "c1"
    assert view[2].tool_call_id == "c1"
    assert view[2].content == "ORION-42"


def test_rebuild_applies_compaction_tail_and_hidden(tmp_path):
    sid = _session(tmp_path)
    projector.project_checkpoint_messages(sid, _messages())
    repository.append_message(
        sid,
        "compaction",
        {
            "content": "summary so far",
            "summary": "summary so far",
            "tail_start_id": "tm1",
            "mode": "auto",
        },
    )

    view = projector.rebuild_model_messages(sid)

    assert view[0].type == "system"
    assert "[历史摘要]" in view[0].content
    assert [m.id for m in view[1:]] == ["tm1", "am_final"]

    hidden = projector.rebuild_model_messages(sid, hidden_ids={"um1"})
    assert [m.id for m in hidden[1:]] == ["tm1", "am_final"]


def test_rebuild_ignores_compaction_after_revert(tmp_path):
    sid = _session(tmp_path)
    projector.project_checkpoint_messages(sid, _messages())
    comp = repository.append_message(
        sid,
        "compaction",
        {"summary": "should be dropped", "tail_start_id": "am_final"},
    )

    view = projector.rebuild_model_messages(
        sid, baseline=int(comp.seq) - 1
    )

    assert all(m.type != "system" for m in view)
    assert [m.id for m in view] == ["um1", "am_tool", "tm1", "am_final"]


def test_project_user_message_survives_without_checkpoint(tmp_path):
    sid = _session(tmp_path)
    projector.project_user_message(sid, "um_x", "hello", [{"name": "a.png"}])

    rows = repository.list_conversation_messages(sid)
    assert [(r.id, r.type) for r in rows] == [("um_x", "user")]
    assert rows[0].data["files"] == [{"name": "a.png"}]


@pytest.mark.asyncio
async def test_session_messages_reads_db_and_shows_compaction_card(tmp_path):
    sid = _session(tmp_path)
    projector.project_checkpoint_messages(sid, _messages())
    repository.append_message(
        sid,
        "compaction",
        {"summary": "cached summary", "tail_start_id": "um1", "mode": "auto"},
    )

    class _Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": []})

    sess = SimpleNamespace(
        hidden_ids=set(),
        compact_baseline_seq=repository.latest_seq(sid),
    )
    ctx = PlatformContext(
        graph=_Graph(),
        registry=SimpleNamespace(),
        vfs=SimpleNamespace(),
        sessions=SimpleNamespace(get=lambda _sid: sess),
        metrics=SimpleNamespace(),
        approvals=SimpleNamespace(),
    )
    items = await ctx.session_messages(sid)

    # 压缩不再物理删除：正文仍可见，并额外插入一张摘要卡。
    assert items[0]["id"] == f"{sid}_compact"
    assert "[历史摘要]" in items[0]["content"]
    # UI 不展示裸 tool 消息与空的工具调用 assistant 轮。
    assert [i["id"] for i in items[1:]] == ["um1", "am_final"]


@pytest.mark.asyncio
async def test_revert_uses_db_ids_and_hides_whole_tail(tmp_path):
    sid = _session(tmp_path)
    projector.project_checkpoint_messages(sid, _messages())
    repository.upsert_epoch(
        sid,
        "old summary",
        repository.latest_seq(sid),
        {"summary": "old summary", "tail_start_id": "tm1", "model_switch": True},
    )

    class _Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": []})

    sess = SimpleNamespace(hidden_ids=set(), compact_baseline_seq=0, updated_at=0.0)
    persisted: list[str] = []
    ctx = PlatformContext(
        graph=_Graph(),
        registry=SimpleNamespace(),
        vfs=SimpleNamespace(),
        sessions=SimpleNamespace(
            get=lambda _sid: sess,
            persist=lambda sid_: persisted.append(sid_),
        ),
        metrics=SimpleNamespace(),
        approvals=SimpleNamespace(),
    )

    out = await ctx.revert_session(sid, "am_tool")

    assert out["deleted"] == 3
    assert sess.hidden_ids == {"am_tool", "tm1", "am_final"}
    assert persisted == [sid]
    assert [i["id"] for i in out["messages"]] == ["um1"]
    epoch = repository.get_epoch(sid)
    assert epoch is not None
    assert "summary" not in epoch.snapshot
    assert "tail_start_id" not in epoch.snapshot


@pytest.mark.asyncio
async def test_run_turn_rebuilds_and_projects_through_db(tmp_path):
    sid = _session(tmp_path)
    projector.project_checkpoint_messages(
        sid,
        [
            HumanMessage(content="old q", id="um1"),
            AIMessage(content="old a", id="am1"),
        ],
    )

    class _Graph:
        def __init__(self):
            self.snapshot = {"messages": []}
            self.calls: list[list[tuple[str, str, str]]] = []

        async def aget_state(self, _config):
            return SimpleNamespace(values=dict(self.snapshot))

        async def ainvoke(self, state, config=None):
            msgs = list(state["messages"].value)
            self.calls.append(
                [(getattr(m, "type", ""), getattr(m, "id", ""), str(m.content)) for m in msgs]
            )
            answer_id = f"am_{len(self.calls) + 1}"
            self.snapshot = {
                "messages": msgs + [AIMessage(content="ok", id=answer_id)],
                "final_output": "ok",
            }
            return dict(self.snapshot)

    graph = _Graph()
    sess = SimpleNamespace(
        hidden_ids=set(),
        compact_baseline_seq=0,
        model=None,
        status="idle",
        directory=str(tmp_path),
        updated_at=0.0,
    )
    ctx = PlatformContext(
        graph=graph,
        registry=SimpleNamespace(),
        vfs=SimpleNamespace(),
        sessions=SimpleNamespace(
            get=lambda _sid: sess,
            touch=lambda _sid: None,
        ),
        metrics=SimpleNamespace(),
        approvals=SimpleNamespace(),
    )

    await ctx._run_turn(None, "new q", session_id=sid, message_id="um2")
    assert [(t, i, c) for t, i, c in graph.calls[0]] == [
        ("human", "um1", "old q"),
        ("ai", "am1", "old a"),
        ("human", "um2", "new q"),
    ]
    assert [r.id for r in repository.list_conversation_messages(sid)] == [
        "um1",
        "am1",
        "um2",
        "am_2",
    ]

    await ctx._run_turn(None, "third q", session_id=sid, message_id="um3")
    assert [i for _t, i, _c in graph.calls[1]] == [
        "um1",
        "am1",
        "um2",
        "am_2",
        "um3",
    ]
    assert [r.id for r in repository.list_conversation_messages(sid)] == [
        "um1",
        "am1",
        "um2",
        "am_2",
        "um3",
        "am_3",
    ]


def test_unknown_session_projection_is_noop():
    sid = f"ses_missing_{uuid.uuid4().hex[:8]}"
    assert projector.project_checkpoint_messages(sid, _messages()) == 0
    projector.project_user_message(sid, "um", "x")
