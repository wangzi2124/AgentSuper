from __future__ import annotations

import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.session import repository
from app.session.compaction import CompactionCheckpoint


def _session(tmp_path) -> str:
    sid = f"ses_ckpt_{uuid.uuid4().hex[:10]}"
    project = repository.resolve_project(str(tmp_path))
    repository.create_session(
        "tester",
        project.id,
        str(tmp_path),
        session_id=sid,
        title="checkpoint",
    )
    return sid


def _project(sid: str) -> None:
    from app.session import projector

    projector.project_checkpoint_messages(
        sid,
        [
            HumanMessage(content="read", id="um1"),
            AIMessage(content="", id="am1", tool_calls=[{"id": "c1", "name": "read_file", "args": {}, "type": "tool_call"}]),
            ToolMessage(content="payload", tool_call_id="c1", name="read_file", id="tm1"),
            AIMessage(content="done", id="am2"),
        ],
    )


def test_record_and_rebuild_use_single_watermark(tmp_path):
    sid = _session(tmp_path)
    _project(sid)
    event = {
        "summary": "summary so far",
        "reason": "auto",
        "tail_start_id": "tm1",
        "tokens_before": 900,
        "tokens_after": 120,
        "model": "fake/model",
    }
    checkpoint = CompactionCheckpoint.record(sid, event)
    assert checkpoint is not None
    assert checkpoint.tail_start_id == "tm1"

    view = CompactionCheckpoint.rebuild_view(sid)
    assert view.summary == "summary so far"
    assert [m.id for m in view.messages] == ["tm1", "am2"]
    assert view.tail_start_id == "tm1"

    epoch = repository.get_epoch(sid)
    assert epoch is not None
    assert epoch.snapshot["summary"] == "summary so far"
    assert epoch.snapshot["tail_start_id"] == "tm1"


def test_record_is_idempotent(tmp_path):
    sid = _session(tmp_path)
    _project(sid)
    event = {"summary": "same", "reason": "auto", "tail_start_id": "tm1"}
    CompactionCheckpoint.record(sid, event)
    CompactionCheckpoint.record(sid, event)
    rows = [m for m in repository.list_messages(sid) if m.type == "compaction"]
    assert len(rows) == 1


def test_missing_tail_does_not_replay_full_history(tmp_path):
    sid = _session(tmp_path)
    _project(sid)
    CompactionCheckpoint.record(
        sid,
        {"summary": "only summary", "reason": "auto", "tail_start_id": "missing"},
    )
    view = CompactionCheckpoint.rebuild_view(sid)
    assert view.summary == "only summary"
    assert view.messages == []


def test_baseline_hides_checkpoint_after_undo(tmp_path):
    sid = _session(tmp_path)
    _project(sid)
    checkpoint = CompactionCheckpoint.record(
        sid,
        {"summary": "s", "reason": "auto", "tail_start_id": "tm1"},
    )
    assert checkpoint is not None
    assert CompactionCheckpoint.latest(sid, baseline=checkpoint.seq - 1) is None
    visible = CompactionCheckpoint.rebuild_view(sid, baseline=checkpoint.seq - 1)
    assert visible.summary == ""
    assert [m.id for m in visible.messages] == ["um1", "am1", "tm1", "am2"]


def test_prune_metadata_survives_reprojection(tmp_path):
    from app.session import projector

    sid = _session(tmp_path)
    _project(sid)
    assert repository.mark_tool_pruned(sid, "tm1", "/tmp/full.txt", 123)

    def _tool(view):
        return next(m for m in view if getattr(m, "id", "") == "tm1")

    first = _tool(projector.rebuild_model_messages(sid))
    assert first.content.startswith("[tool output pruned")
    assert "/tmp/full.txt" in first.content

    # A later checkpoint projection rewrites the row; prune metadata must survive.
    _project(sid)
    second = _tool(projector.rebuild_model_messages(sid))
    assert second.content.startswith("[tool output pruned")

    original = next(
        m for m in repository.list_conversation_messages(sid) if m.id == "tm1"
    )
    assert original.data["content"] == "payload"
    assert original.data["prune"]["tokens"] == 123


def test_repair_reconciles_epoch_cache(tmp_path):
    sid = _session(tmp_path)
    _project(sid)
    checkpoint = CompactionCheckpoint.record(
        sid,
        {"summary": "repair me", "reason": "auto", "tail_start_id": "tm1"},
    )
    assert checkpoint is not None
    repository.upsert_epoch(sid, "", 0, {})

    CompactionCheckpoint.repair(sid)

    epoch = repository.get_epoch(sid)
    assert epoch is not None
    assert epoch.snapshot["summary"] == "repair me"
    assert epoch.snapshot["tail_start_id"] == "tm1"
