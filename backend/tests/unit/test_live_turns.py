from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import live_turns
from app.live_turns import LiveTurnRecorder, reconcile_live_turns, sidecar_path


def test_recorder_accumulates_and_writes_sidecar():
    recorder = LiveTurnRecorder("sess-1", "turn-1", throttle_sec=0)
    recorder.observe({"type": "text_delta", "delta": "部分"})
    recorder.observe({"type": "text_delta", "delta": "正文"})
    recorder.observe(
        {
            "type": "agent_step",
            "agent_id": "build",
            "step": {"step_id": "st1", "tool_name": "read_file"},
        }
    )
    recorder.note_assistant_id("am_1")
    recorder.flush("running")

    data = json.loads(sidecar_path("sess-1", "turn-1").read_text(encoding="utf-8"))
    assert data["text"] == "部分正文"
    assert data["assistant_msg_id"] == "am_1"
    assert data["agents"][0]["steps"][0]["step_id"] == "st1"
    assert data["status"] == "running"
    recorder.clear()


def test_recorder_clears_on_success():
    recorder = LiveTurnRecorder("sess-2", "turn-2", throttle_sec=0)
    recorder.observe({"type": "text_delta", "delta": "x"})
    path = sidecar_path("sess-2", "turn-2")
    assert path.exists()
    recorder.clear()
    assert not path.exists()


def test_recorder_does_not_rewrite_sidecar_after_clear():
    recorder = LiveTurnRecorder("sess-4", "turn-4", throttle_sec=0)
    recorder.observe({"type": "text_delta", "delta": "半截"})
    path = sidecar_path("sess-4", "turn-4")
    recorder.clear()
    # 终态事件在 clear 之后到达：不得把 sidecar 写回来（重启会误导入）。
    recorder.observe({"type": "done", "answer": "完整答案"})
    recorder.flush("running")
    assert not path.exists()

    # 下一轮 drain 重置后可继续记录。
    recorder.reset()
    recorder.observe({"type": "text_delta", "delta": "第二轮"})
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["text"] == "第二轮"
    recorder.clear()


@pytest.mark.asyncio
async def test_reconcile_imports_partial_and_marks_session_interrupted(monkeypatch):
    for leftover in live_turns.live_dir().glob("*.json"):
        leftover.unlink(missing_ok=True)
    recorder = LiveTurnRecorder("sess-3", "turn-3", throttle_sec=0)
    recorder.observe({"type": "text_delta", "delta": "半截回答"})
    recorder.note_assistant_id("am_partial")
    recorder.flush("interrupted")

    writes: list[dict] = []

    class _Graph:
        async def aupdate_state(self, config, payload):  # noqa: ANN001
            writes.append(payload)

    async def _make_state(_session_id):
        return {"messages": []}

    ctx = SimpleNamespace(
        state=_make_state,
        graph=_Graph(),
        sessions=SimpleNamespace(get=lambda sid: object()),
    )

    updates: list[tuple[str, str]] = []
    cleared: list[str] = []

    from app.session import repository as repo

    monkeypatch.setattr(repo, "update_session", lambda sid, **kw: updates.append((sid, kw)))
    monkeypatch.setattr(repo, "clear_inputs", lambda sid: cleared.append(sid))
    monkeypatch.setattr(repo, "list_sessions_by_status", lambda status: [])

    stats = await reconcile_live_turns(ctx)

    assert stats["imported"] == 1
    assert writes
    message = writes[0]["messages"][0]
    assert message.content == "半截回答"
    assert message.id == "am_partial"
    assert message.additional_kwargs["ui"]["partial_error"] == "服务重启，任务已中断"
    assert ("sess-3", {"status": "interrupted"}) in updates
    assert "sess-3" in cleared
    assert not sidecar_path("sess-3", "turn-3").exists()


@pytest.mark.asyncio
async def test_reconcile_scans_running_sessions(monkeypatch):
    from app.session import repository as repo

    class _Graph:
        async def aupdate_state(self, config, payload):  # noqa: ANN001
            raise AssertionError("no sidecar writes expected")

    ctx = SimpleNamespace(
        state=lambda sid: None,
        graph=_Graph(),
        sessions=SimpleNamespace(get=lambda sid: None),
    )

    updates: list[str] = []
    cleared: list[str] = []
    monkeypatch.setattr(repo, "update_session", lambda sid, **kw: updates.append(sid))
    monkeypatch.setattr(repo, "clear_inputs", lambda sid: cleared.append(sid))
    monkeypatch.setattr(
        repo,
        "list_sessions_by_status",
        lambda status: [SimpleNamespace(id="sess-running")],
    )

    await reconcile_live_turns(ctx)
    assert "sess-running" in updates
    assert "sess-running" in cleared


def test_sidecar_path_is_sanitized():
    path = sidecar_path("../../evil", "turn/./1")
    assert ".." not in path.name
    assert path.parent == live_turns.live_dir()
