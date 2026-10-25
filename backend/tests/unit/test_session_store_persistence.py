"""L1 单元测试：SessionStore 落库 + 重启水合（app/deps.py + app/session）。

覆盖：create 写 session.db（含 project FK 补行）、update/persist 整份回写、
hidden_ids/compact_baseline_seq 持久化、全新 store（模拟重启）get/list 水合、
delete 级联清行。库路径由 conftest 隔离到临时 session.db。
"""

from __future__ import annotations

from app.deps import SessionStore
from app.session import repository as repo


def test_create_update_delete_write_through(tmp_path):
    store = SessionStore()
    sess = store.create(
        title="落库测试",
        directory=str(tmp_path),
        user_id="u_it_store",
        model="ollama_chat/qwen2.5:3b",
    )
    try:
        info = repo.get_session(sess.id)
        assert info is not None
        assert info.title == "落库测试"
        assert info.user_id == "u_it_store"
        assert info.project_id  # FK：resolve_project 已补项目行
        # str 模型引用读回后归一为 ModelRef（曾是 pydantic ValidationError）
        assert info.model is not None and info.model.id == "ollama_chat/qwen2.5:3b"

        store.update(sess.id, title="改名", status="running")
        info2 = repo.get_session(sess.id)
        assert info2 is not None
        assert info2.title == "改名"
        assert info2.status == "running"
        assert info2.time_updated >= info.time_created

        assert store.delete(sess.id) is True
        assert repo.get_session(sess.id) is None
        assert store.get(sess.id) is None
    finally:
        store.delete(sess.id)  # 幂等清理


def test_hidden_ids_and_baseline_persist_and_hydrate():
    store = SessionStore()
    sess = store.create(title="revert 状态", user_id="u_it_store")
    hidden_id = f"{sess.id}_m2"
    try:
        sess.hidden_ids.add(hidden_id)
        sess.compact_baseline_seq = 3
        store.persist(sess.id)

        info = repo.get_session(sess.id)
        assert info is not None
        assert info.hidden_ids == [hidden_id]
        assert info.compact_baseline_seq == 3

        # 模拟重启：全新 store（内存空）→ get/list 从库水合
        fresh = SessionStore()
        revived = fresh.get(sess.id)
        assert revived is not None
        assert revived.title == "revert 状态"
        assert revived.hidden_ids == {hidden_id}
        assert revived.compact_baseline_seq == 3
        listed_ids = [s.id for s in fresh.list(user_id="u_it_store")]
        assert sess.id in listed_ids
    finally:
        store.delete(sess.id)
        assert repo.get_session(sess.id) is None
