# -*- coding: utf-8 -*-
"""session/repository.py 全量用例（temp DB，monkeypatch repository._get_db）。

覆盖：项目 CRUD、会话 CRUD/列表过滤/更新/子级/递归删除、消息 append/list/seq/
compaction/seq_of_message/usage、parts append/list/批量分片/update、
update/delete message、revert（回滚水位+级联删 parts）、上下文纪元、输入队列
（admit/promote steer 优先/pending/count/clear）。
运行：pytest tests/test_session_repository.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.session.repository as repo
import app.session.db as sdb
from app.session.models import ContextEpoch


@pytest.fixture
def db_conn(monkeypatch, tmp_path):
    path = tmp_path / "session_test.db"

    def fake_get_db():
        return sdb._get_db(path)

    monkeypatch.setattr(repo, "_get_db", fake_get_db)
    return path


def _proj(root):
    return repo.resolve_project(root, name=root, vcs="git").id


def _session(db_conn, user="u1", project_id=None, **kw):
    pid = project_id or _proj("/default")
    defaults = dict(user_id=user, project_id=pid, directory="/d", title="会话")
    defaults.update(kw)
    return repo.create_session(**defaults)


# ── 项目 ───────────────────────────────────────────────────────────────────

def test_resolve_and_get_project(db_conn):
    p = repo.resolve_project("/root", name="R", vcs="git")
    assert p.id and p.name == "R" and p.root == "/root"
    again = repo.resolve_project("/root", name="R2", vcs="git")
    assert again.id == p.id  # upsert 同 id
    got = repo.get_project(p.id)
    assert got is not None and got.name == "R2"
    assert repo.get_project("nope") is None


# ── 会话 ───────────────────────────────────────────────────────────────────

def test_create_get_require_session(db_conn):
    s = _session(db_conn)
    assert s.id and s.kind == "multi-agent" and s.status == "idle"
    assert s.title == "会话"
    got = repo.get_session(s.id)
    assert got.id == s.id
    assert repo.require_session(s.id).id == s.id
    with pytest.raises(repo.SessionNotFound):
        repo.require_session("missing")


def test_create_session_custom_fields(db_conn):
    s = _session(db_conn, agent="rag", model={"id": "m", "providerID": "deepseek"}, kind="multi-agent",
                 parent_id=None, session_id="ses_fixed")
    assert s.id == "ses_fixed"
    assert s.agent == "rag"
    assert s.model.id == "m"
    assert s.model.providerID == "deepseek"


def test_create_session_model_without_provider_id(db_conn):
    """回归：ModelRef.providerID 允许缺省（此前 get_session 读回缺 providerID 的
    model JSON 会抛 ValidationError → 会话列表/详情全崩）。"""
    s = _session(db_conn, model={"id": "m"}, session_id="ses_partial")
    assert s.model is not None
    assert s.model.id == "m"
    assert s.model.providerID == ""
    got = repo.get_session(s.id)
    assert got.model.id == "m"
    assert got.model.providerID == ""


def test_list_sessions_filters(db_conn):
    p1 = _proj("/p1")
    p2 = _proj("/p2")
    a = _session(db_conn, user="u1", project_id=p1, title="hello 世界")
    b = _session(db_conn, user="u1", project_id=p2)
    _session(db_conn, user="u2", project_id=p1)
    repo.update_session(a.id, archived=True)

    # 默认 archived=False → 过滤已归档 a
    assert {s.id for s in repo.list_sessions("u1")} == {b.id}
    # archived=True → 不过滤
    assert {s.id for s in repo.list_sessions("u1", archived=True)} >= {a.id, b.id}
    # project 过滤（a 已归档被默认过滤 → 空）
    assert {s.id for s in repo.list_sessions("u1", project_id=p1)} == set()
    assert {s.id for s in repo.list_sessions("u1", project_id=p1, archived=True)} == {a.id}
    # kind / search
    assert {s.id for s in repo.list_sessions("u1", kind="multi-agent")} == {b.id}
    assert {s.id for s in repo.list_sessions("u1", search="世界")} == set()
    assert {s.id for s in repo.list_sessions("u1", search="世界", archived=True)} == {a.id}

    # roots_only：仅父级（b 为父，child 挂 a 下）
    child = repo.create_session("u1", p1, "/d", parent_id=a.id, session_id="ses_child")
    assert {s.id for s in repo.list_sessions("u1", roots_only=True)} == {b.id}
    # workspace 过滤
    assert {s.id for s in repo.list_sessions("u1", workspace_id="w")} == set()


def test_update_session(db_conn):
    s = _session(db_conn)
    upd = repo.update_session(s.id, title="新标题", archived=True, unknown_field=1)
    assert upd.title == "新标题"
    assert upd.time_archived is not None
    repo.update_session(s.id, archived=False)
    assert repo.get_session(s.id).time_archived is None
    # 无有效字段 → 直接返回
    same = repo.update_session(s.id, bogus=2)
    assert same.title == "新标题"


def test_list_children_and_remove_cascade(db_conn):
    parent = _session(db_conn)
    child = repo.create_session("u1", _proj("/default"), "/d", parent_id=parent.id)
    assert [c.id for c in repo.list_children(parent.id)] == [child.id]
    repo.remove_session(parent.id)
    assert repo.get_session(parent.id) is None
    assert repo.get_session(child.id) is None


# ── 消息 ───────────────────────────────────────────────────────────────────

def test_append_message_seq_increments(db_conn):
    s = _session(db_conn)
    m1 = repo.append_message(s.id, "user", {"text": "hi"})
    m2 = repo.append_message(s.id, "user", {"text": "yo"})
    assert m1.seq == 1 and m2.seq == 2
    assert m1.type == "user" and m1.data == {"text": "hi"}


def test_list_messages_after_seq_and_limit(db_conn):
    s = _session(db_conn)
    for i in range(5):
        repo.append_message(s.id, "user", {"i": i})
    msgs = repo.list_messages(s.id)
    assert len(msgs) == 5
    assert [m.data["i"] for m in repo.list_messages(s.id, after_seq=2)] == [2, 3, 4]
    assert len(repo.list_messages(s.id, limit=2)) == 2


def test_latest_seq_and_compaction(db_conn):
    s = _session(db_conn)
    assert repo.latest_seq(s.id) == 0
    repo.append_message(s.id, "user", {})
    repo.append_message(s.id, "assistant", {})
    repo.append_message(s.id, "compaction", {"summary": "x"})
    assert repo.latest_seq(s.id) == 3
    assert repo.latest_compaction_seq(s.id) == 3
    assert repo.latest_compaction_seq("other") is None
    m = repo.append_message(s.id, "user", {})
    assert repo.seq_of_message(s.id, m.id) == 4
    assert repo.seq_of_message(s.id, "nope") is None


def test_add_session_usage(db_conn):
    s = _session(db_conn)
    repo.add_session_usage(s.id, input_tokens=10, output_tokens=5, cost=0.01)
    repo.add_session_usage(s.id, input_tokens=10)
    got = repo.get_session(s.id)
    assert got.tokens_input == 20 and got.tokens_output == 5
    assert abs(got.cost - 0.01) < 1e-9


# ── parts ──────────────────────────────────────────────────────────────────

def test_append_list_update_part(db_conn):
    s = _session(db_conn)
    m = repo.append_message(s.id, "assistant", {})
    p1 = repo.append_part(s.id, m.id, "text", {"text": "hello"})
    p2 = repo.append_part(s.id, m.id, "tool", {"name": "t"})
    parts = repo.list_parts(m.id)
    assert len(parts) == 2
    assert parts[0].type == "text"
    upd = repo.update_part(s.id, p1.id, {"text": "updated"})
    assert upd.data == {"text": "updated"}
    assert repo.update_part(s.id, "missing", {}) is None
    by_msg = repo.list_parts_for_messages([m.id])
    assert len(by_msg[m.id]) == 2


def test_parts_batch_sharding(db_conn):
    """>SQLite 变量上限（500）的 message_id 集合触发分批查询。"""
    s = _session(db_conn)
    m1 = repo.append_message(s.id, "user", {})
    repo.append_part(s.id, m1.id, "text", {"text": "A"})
    ids = [f"msg_{i}" for i in range(550)]
    out = repo.list_parts_for_messages(ids)
    assert m1.id not in ids  # m1 不在查询集内
    assert out == {}
    out2 = repo.list_parts_for_messages([m1.id] + ids)
    assert out2[m1.id][0].data == {"text": "A"}


def test_update_and_delete_message(db_conn):
    s = _session(db_conn)
    m = repo.append_message(s.id, "assistant", {"answer": "old"})
    upd = repo.update_message(s.id, m.id, {"answer": "new"})
    assert upd.data == {"answer": "new"}
    assert repo.update_message(s.id, "nope", {}) is None
    repo.append_part(s.id, m.id, "text", {"text": "x"})
    assert repo.delete_message(s.id, m.id) is True
    assert repo.list_parts(m.id) == []
    assert repo.delete_message(s.id, m.id) is False


# ── revert ─────────────────────────────────────────────────────────────────

def test_revert_to_message(db_conn):
    s = _session(db_conn)
    m1 = repo.append_message(s.id, "user", {})
    m2 = repo.append_message(s.id, "assistant", {})
    m3 = repo.append_message(s.id, "assistant", {})
    repo.append_part(s.id, m3.id, "text", {"text": "doomed"})
    repo.upsert_epoch(s.id, "base", 3, {})
    deleted = repo.revert_to_message(s.id, m2.id)
    assert deleted == 1  # 只删 m3
    assert [m.id for m in repo.list_messages(s.id)] == [m1.id, m2.id]
    assert repo.list_parts(m3.id) == []
    epoch = repo.get_epoch(s.id)
    assert epoch.baseline_seq == 0  # 回滚到最近 compaction（无）


def test_revert_keeps_compaction_baseline(db_conn):
    s = _session(db_conn)
    m1 = repo.append_message(s.id, "compaction", {})
    m2 = repo.append_message(s.id, "user", {})
    m3 = repo.append_message(s.id, "assistant", {})
    repo.upsert_epoch(s.id, "base", 3, {})
    deleted = repo.revert_to_message(s.id, m1.id)
    assert deleted == 2
    epoch = repo.get_epoch(s.id)
    assert epoch.baseline_seq == 1  # 保留 compaction 水位


def test_revert_unknown_message(db_conn):
    s = _session(db_conn)
    with pytest.raises(repo.MessageNotFound):
        repo.revert_to_message(s.id, "ghost")


# ── 上下文纪元 ─────────────────────────────────────────────────────────────

def test_epoch_upsert_get(db_conn):
    s = _session(db_conn)
    assert repo.get_epoch(s.id) is None
    repo.upsert_epoch(s.id, "base1", 1, {"tail": 1})
    e = repo.get_epoch(s.id)
    assert isinstance(e, ContextEpoch)
    assert e.baseline == "base1" and e.baseline_seq == 1 and e.snapshot == {"tail": 1}
    repo.upsert_epoch(s.id, "base2", 5, {"tail": 2})
    assert repo.get_epoch(s.id).baseline_seq == 5


# ── 输入队列 ───────────────────────────────────────────────────────────────

def test_inputs_admit_promote_priority(db_conn):
    s = _session(db_conn)
    i1 = repo.admit_input(s.id, {"prompt": "queue"}, delivery="queue")
    i2 = repo.admit_input(s.id, {"prompt": "steer"})
    assert i1 and i2
    assert repo.count_pending(s.id) == 2
    assert repo.has_pending(s.id) is True
    assert repo.has_pending(s.id, delivery="queue") is True
    assert repo.has_pending(s.id, delivery="steer") is True
    first = repo.promote_next(s.id)
    assert first["delivery"] == "steer"  # steer 优先于 queue
    assert first["prompt"] == {"prompt": "steer"}
    assert repo.count_pending(s.id) == 1
    second = repo.promote_next(s.id)
    assert second["delivery"] == "queue"
    assert repo.promote_next(s.id) is None
    assert repo.has_pending(s.id) is False


def test_inputs_clear(db_conn):
    s = _session(db_conn)
    repo.admit_input(s.id, {"p": 1})
    repo.admit_input(s.id, {"p": 2})
    assert repo.clear_inputs(s.id) == 2
    assert repo.count_pending(s.id) == 0
    assert repo.clear_inputs(s.id) == 0