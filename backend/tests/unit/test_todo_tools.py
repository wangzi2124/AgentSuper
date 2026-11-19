from __future__ import annotations

from app.session import repository
from app.session.db import _get_db
from execution.tools import todo_tools


def _store(tmp_path, monkeypatch):
    path = tmp_path / "session.db"

    def _open():
        return _get_db(path)

    monkeypatch.setattr(repository, "_get_db", _open)
    project = repository.resolve_project(str(tmp_path))
    return repository.create_session(
        "u1", project.id, str(tmp_path), title="todo tools"
    )


async def test_todo_write_requires_session(monkeypatch):
    monkeypatch.setattr(todo_tools, "current_session_id", lambda: "")
    result = await todo_tools.todo_write([{"title": "读取文件"}])
    assert "没有活动会话" in result


async def test_todo_write_persists_snapshot_and_emits_event(tmp_path, monkeypatch):
    sess = _store(tmp_path, monkeypatch)
    emitted: list[dict] = []

    async def fake_emit(ctx, event_type, data, **kwargs):
        emitted.append({"type": event_type, "data": data, "kwargs": kwargs})

    monkeypatch.setattr(todo_tools, "current_session_id", lambda: sess.id)
    monkeypatch.setattr(todo_tools, "current_platform_ctx", lambda: object())
    monkeypatch.setattr(todo_tools, "emit_event", fake_emit)

    result = await todo_tools.todo_write(
        [
            {"title": "读取配置", "status": "completed"},
            {"title": "执行任务", "agent": "build"},
        ],
        plan_id="plan_1",
        tier="serial",
    )

    assert "revision 1" in result
    plan = repository.get_plan(sess.id, "plan_1")
    assert plan is not None
    assert plan["revision"] == 1
    assert plan["tier"] == "serial"
    assert [step["id"] for step in plan["steps"]] == ["s1", "s2"]
    assert [step["status"] for step in plan["steps"]] == ["completed", "pending"]
    assert plan["steps"][1]["agent"] == "build"
    assert len(emitted) == 1
    assert emitted[0]["type"] == "session.plan.updated"
    assert emitted[0]["data"]["revision"] == 1
    assert emitted[0]["kwargs"]["session_id"] == sess.id

    result = await todo_tools.todo_write(
        [
            {"title": "读取配置", "status": "completed"},
            {"title": "执行任务", "status": "in_progress"},
            {"title": "汇总结果"},
        ]
    )
    assert "revision 2" in result
    plan = repository.get_plan(sess.id)
    assert plan is not None
    assert plan["revision"] == 2
    assert [step["status"] for step in plan["steps"]] == [
        "completed",
        "in_progress",
        "pending",
    ]
    assert emitted[-1]["data"]["revision"] == 2


async def test_todo_write_rejects_empty_and_stale(tmp_path, monkeypatch):
    sess = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(todo_tools, "current_session_id", lambda: sess.id)
    monkeypatch.setattr(todo_tools, "current_platform_ctx", lambda: None)
    assert "格式无效" in await todo_tools.todo_write([])

    assert await todo_tools.todo_write([{"title": "第一步"}], plan_id="plan_1") != ""

    def reject_stale(*args, **kwargs):
        return False

    monkeypatch.setattr(todo_tools.repo, "save_plan", reject_stale)
    stale = await todo_tools.todo_write(
        [{"title": "第一步"}, {"title": "第二步"}], plan_id="plan_1"
    )
    assert "已过期" in stale
