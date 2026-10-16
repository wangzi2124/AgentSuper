# -*- coding: utf-8 -*-
"""D1 seq 竞态：并发写入会话消息的 seq 原子性用例。

语义与 repository.py append_message 一致：
  BEGIN IMMEDIATE + INSERT ... VALUES ((SELECT COALESCE(MAX(seq),0)+1 FROM session_messages WHERE session_id=?))
在多个线程并发对同一会话写消息时，seq 必须严格唯一且连续（1..N），无重复/错乱。
运行：pytest tests/test_session_seq.py
"""
import os
import sqlite3
import sys
import threading

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

_INSERT_SQL = (
    "INSERT INTO session_messages(session_id, seq, msg_type, data) VALUES (?,"
    "(SELECT COALESCE(MAX(seq), 0) + 1 FROM session_messages WHERE session_id = ?), ?, ?)"
)


def test_concurrent_append_unique_seqs(tmp_path):
    db_path = str(tmp_path / "seq.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE session_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, seq INTEGER, msg_type TEXT, data TEXT)"
    )
    conn.commit()
    conn.close()

    n_threads = 20
    msgs_per_session = 5
    errors: list[Exception] = []

    def writer(tid):
        try:
            sid = f"session-{tid % 4}"
            for i in range(msgs_per_session):
                db = sqlite3.connect(db_path, timeout=30)
                try:
                    db.execute("BEGIN IMMEDIATE")  # 写锁：与其他写者串行
                    db.execute(
                        _INSERT_SQL, (sid, sid, "user", f"t{tid}")
                    )
                    db.commit()
                finally:
                    db.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"write errors: {errors[:5]}"

    db = sqlite3.connect(db_path)
    rows = db.execute(
        "SELECT session_id, seq, COUNT(*) FROM session_messages GROUP BY session_id, seq HAVING COUNT(*) > 1"
    ).fetchall()
    assert rows == [], f"duplicate seqs: {rows}"

    for sid in ("session-0", "session-1", "session-2", "session-3"):
        seqs = [r[0] for r in db.execute(
            "SELECT seq FROM session_messages WHERE session_id=?", (sid,)
        )]
        expected = set(range(1, n_threads // 4 * msgs_per_session + 1))
        assert set(seqs) == expected, (sid, sorted(seqs))
        assert len(seqs) == len(set(seqs)), sid
    db.close()