from __future__ import annotations

from harness.contracts import ToolPartProjection
from harness.part_projection import AgentPartProjector


def _tool_event(event_type: str, **extra):
    return {
        "type": event_type,
        "part_id": "call_1",
        "step_id": "tool-call_1",
        "agent_id": "build",
        "agent_name": "Build",
        "tool_name": "read_file",
        "tool_args": {"path": "hello.txt"},
        **extra,
    }


def test_tool_lifecycle_starts_agent_once_and_tracks_snapshot():
    projector = AgentPartProjector()

    started = projector.project(_tool_event("tool_start"))
    assert [event["type"] for event in started] == ["agent_start", "agent_step"]
    assert started[1]["step"]["tool_name"] == "read_file"
    assert started[1]["step"]["tool_args"] == {"path": "hello.txt"}
    assert started[1]["step"]["part_id"] == "call_1"

    finished = projector.project(
        _tool_event(
            "tool_end",
            status="failed",
            tool_result="boom",
            duration_ms=2.5,
            error="denied",
        )
    )
    assert [event["type"] for event in finished] == ["agent_step"]
    step = finished[0]["step"]
    assert step["status"] == "failed"
    assert step["tool_result"] == "boom"
    assert step["duration_ms"] == 2.5

    snapshot = projector.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["agent_id"] == "build"
    assert len(snapshot[0]["steps"]) == 1
    assert snapshot[0]["steps"][0]["status"] == "failed"


def test_track_upserts_steps_and_finishes_agent():
    projector = AgentPartProjector()

    projector.track(
        {
            "type": "agent_step",
            "agent_id": "worker",
            "agent_name": "Worker",
            "step": {"step_id": "s1", "name": "first", "status": "running"},
        }
    )
    projector.track(
        {
            "type": "agent_step",
            "agent_id": "worker",
            "agent_name": "Worker 2",
            "agent_avatar": "avatar.png",
            "step": {"step_id": "s1", "name": "first", "status": "completed"},
        }
    )
    projector.track({"type": "agent_done", "agent_id": "worker", "content": "done"})

    agent = projector.agents["worker"]
    assert agent["agent_name"] == "Worker 2"
    assert agent["agent_avatar"] == "avatar.png"
    assert agent["status"] == "completed"
    assert agent["content"] == "done"
    assert len(agent["steps"]) == 1
    assert agent["steps"][0]["status"] == "completed"

    projector.track({"type": "agent_step", "agent_id": "worker", "step": {}})
    projector.track({"type": "unknown", "agent_id": "worker"})
    assert len(projector.agents["worker"]["steps"]) == 1


def test_agent_error_marks_snapshot_failed():
    projector = AgentPartProjector()

    events = projector.project(
        {
            "type": "agent_end",
            "agent_id": "worker",
            "agent_name": "Worker",
            "status": "failed",
            "error": "upstream failed",
        }
    )

    assert [event["type"] for event in events] == ["agent_start", "agent_error"]
    assert events[1]["error"] == "upstream failed"
    assert projector.agents["worker"]["status"] == "failed"


def test_context_truncated_and_text_delta_projection():
    projector = AgentPartProjector()

    events = projector.project(
        {
            "type": "context_truncated",
            "agent_id": "build",
            "agent_name": "Build",
            "model": "m",
            "window": 8,
            "budget": 6,
            "reserve": 2,
            "estimated_before": 10,
            "estimated_after": 5,
            "dropped_messages": 2,
        }
    )
    assert [event["type"] for event in events] == ["agent_start", "context_truncated"]
    assert events[1]["dropped_messages"] == 2

    assert projector.project({"type": "text_delta", "delta": "hi"}) == [
        {"type": "text_delta", "agent_id": "", "delta": "hi"}
    ]
    assert projector.project({"type": "text_delta", "delta": ""}) == []
    assert projector.project({"type": "unknown"}) == []


def test_projector_ignores_malformed_tool_events_and_duplicate_agent_start():
    projector = AgentPartProjector()

    assert projector.project({"type": "tool_start", "agent_id": "build"}) == []
    assert projector.start_agent("build", "Build") is not None
    assert projector.start_agent("build", "Build") is None
    assert projector.project(
        {"type": "agent_start", "agent_id": "build", "agent_name": "Build"}
    ) == []


def test_tool_part_projection_falls_back_to_part_id_and_rejects_empty():
    projection = ToolPartProjection.from_event(
        {
            "type": "tool_start",
            "part_id": "call_only",
            "agent_id": "build",
            "name": "ls",
        }
    )
    assert projection is not None
    assert projection.step_id == "call_only"
    assert projection.part_id == "call_only"
    assert projection.to_step()["tool_name"] == "ls"
    assert ToolPartProjection.from_event({"type": "tool_start"}) is None
    assert ToolPartProjection.from_event({"type": "other", "step_id": "s"}) is None
