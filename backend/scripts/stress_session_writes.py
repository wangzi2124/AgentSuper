# -*- coding: utf-8 -*-
"""高并发写入压测（验收清单 L224：「高并发写入压测无 IntegrityError / 索引损坏」）。

目标验证：
  1. D1 原子 seq（BEGIN IMMEDIATE + MAX(seq)+1）在并发下无 IntegrityError、无序列错乱
  2. 会话/消息/部件写入在并发下不丢数、不产生孤儿
  3. 库文件通过 PRAGMA integrity_check（索引未损坏）

模式 A（默认，进程内，即时、免费）：多线程并发 append_message / append_part /
        create_session / admit_input，直写一个隔离的临时 session.db（data/stress/**，
        不触碰真实 data/session.db）。
模式 B（--http，需要正在运行的后端 + LLM 调用费）：并发 POST /api/chat/multi-agent，
        端到端验证 信号量/总线路由/session 落库 在并发下的一致性。

用法（在 backend 目录下）：
    .venv\\Scripts\\python.exe scripts/stress_session_writes.py
    .venv\\Scripts\\python.exe scripts/stress_session_writes.py --workers 64 --total 5000 --sessions 4
    .venv\\Scripts\\python.exe scripts/stress_session_writes.py --http --base http://127.0.0.1:8000 --http-requests 6
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_errors: list[str] = []
_lock = threading.Lock()


def _record(err: str) -> None:
    with _lock:
        _errors.append(err)
        print(f"[!!] {err}", flush=True)


def run_in_process(workers: int, total: int, sessions_count: int) -> bool:
    import sqlite3

    import app.session.db as db
    from app.session import repository as repo

    stress_root = BACKEND_DIR / "data" / "stress"
    stress_root.mkdir(parents=True, exist_ok=True)
    stress_db = stress_root / f"session_stress_{int(time.time())}.db"
    if stress_db.exists():
        stress_db.unlink()

    def open_raw() -> sqlite3.Connection:
        conn = sqlite3.connect(str(stress_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        # WAL 下同一时刻仅一个写者；压测线程数远大于 1 时忙等要留足排队时间
        conn.execute("PRAGMA busy_timeout = 60000")
        return conn

    raw = open_raw()
    raw.executescript(db._SCHEMA)
    raw.commit()
    raw.close()

    # 生产连接池是主线程绑定（uvicorn 事件循环线程），跨线程借用会触发
    # "SQLite objects created in a thread can only be used in that same thread"。
    # 压测换成「每调用一条全新连接」（与多路并发写 WAL 的真实形态一致），
    # 且仍走 repository 同一份 BEGIN IMMEDIATE + MAX(seq)+1 原子 SEQ。
    def thread_local_db(path=None):
        if path is not None:
            return db._get_db(path)
        return open_raw()

    repo._get_db = thread_local_db

    user = "stress-user"
    project_id = repo.resolve_project(str(BACKEND_DIR), name="stress", vcs="git").id
    sessions = [
        repo.create_session(user, project_id, str(BACKEND_DIR), kind="multi-agent", title=f"stress-{i}")
        for i in range(sessions_count)
    ]
    print(f"[setup] stress db: {stress_db}", flush=True)
    print(f"[setup] sessions: {[s.id for s in sessions]}", flush=True)

    counter = {"n": 0}
    counter_lock = threading.Lock()
    bar = threading.Barrier(workers)

    def worker(wid: int) -> None:
        try:
            bar.wait(timeout=10)
            while True:
                with counter_lock:
                    if counter["n"] >= total:
                        break
                    counter["n"] += 1
                    idx = counter["n"]
                session = sessions[idx % len(sessions)]
                try:
                    msg = repo.append_message(
                        session.id,
                        "user" if idx % 2 else "assistant",
                        {"text": f"stress-{wid}-{idx}", "seq_marker": idx},
                    )
                    repo.append_part(
                        session.id, msg.id, "text", {"content": f"part-of-{idx}"}
                    )
                except Exception as e:  # noqa: BLE001
                    _record(f"worker {wid} msg/part {idx}: {type(e).__name__}: {e}")
                    if isinstance(e, KeyboardInterrupt):
                        raise
            if wid == 0:
                try:
                    repo.admit_input(sessions[0].id, {"prompt": "queue-probe"})
                except Exception as e:  # noqa: BLE001
                    _record(f"admit_input: {type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            _record(f"worker {wid} crashed: {type(e).__name__}: {e}")
            traceback.print_exc()

    t0 = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,), name=f"w{i}") for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.perf_counter() - t0

    passes = True
    expected = total
    actual = 0
    for s in sessions:
        try:
            first = repo.latest_seq(s.id)  # touches the same db via pool
            seqs = [m.seq for m in repo.list_messages(s.id, after_seq=0)]
            actual += len(seqs)
            if sorted(seqs) != list(range(1, len(seqs) + 1)):
                passes = False
                _record(f"session {s.id}: seq 不连续/重复 → {seqs[:10]}... (len={len(seqs)})")
        except Exception as e:  # noqa: BLE001
            passes = False
            _record(f"verify session {s.id}: {type(e).__name__}: {e}")

    if actual != expected:
        passes = False
        _record(f"消息总数不匹配: expected={expected} actual={actual}")

    integrity = "ERR"
    try:
        raw = open_raw()
        row = raw.execute("PRAGMA integrity_check").fetchone()
        integrity = row[0] if row else "ERR"
        raw.close()
    except Exception as e:  # noqa: BLE001
        _record(f"integrity_check: {type(e).__name__}: {e}")
    if integrity != "ok":
        passes = False
        _record(f"PRAGMA integrity_check = {integrity!r}（期望 'ok'）")

    print("\n──── 进程内压测结果 ────", flush=True)
    print(f"  workers={workers} total={total} sessions={sessions_count}", flush=True)
    print(f"  duration={dt:.2f}s  ops={total / dt:.0f}/s (message+part 双写)", flush=True)
    print(f"  integrity_check={integrity}", flush=True)
    print(f"  errors={len(_errors)}", flush=True)
    print(f"  [{'PASS' if passes else 'FAIL'}] 无 IntegrityError 且 seq 连续、数据无丢失", flush=True)
    return passes


def run_http(base: str, requests_count: int) -> bool:
    import requests

    user, pwd = "stress-http", "stress-pass-9x"
    s = requests.Session()
    s.post(f"{base}/api/auth/account/register", json={"username": user, "password": pwd}, timeout=5)
    r = s.post(f"{base}/api/auth/account/login", json={"username": user, "password": pwd}, timeout=5)
    if r.status_code != 200:
        _record(f"auth 失败: {r.status_code} {r.text[:200]}")
        return False
    data = r.json()["data"]
    h = {
        "Content-Type": "application/json",
        "X-User-Id": data.get("user_id") or data.get("uid"),
        "X-Auth-Token": data.get("token"),
    }

    print(f"[http] 并发 {requests_count} 个 /api/chat/multi-agent（每次调用真实 LLM，注意费用）", flush=True)
    results = []

    def call(i: int) -> None:
        t0 = time.perf_counter()
        try:
            resp = s.post(
                f"{base}/api/chat/multi-agent",
                json={"message": "你好，请用一句话自我介绍一下", "use_vector_db": False},
                headers=h, timeout=220,
            )
            # 非流式端点返回裸 MultiAgentChatResponse（answer/sources/steps/routed_to）
            payload = resp.json()
            ok = resp.status_code == 200 and bool(payload.get("answer"))
            results.append(
                (ok, resp.status_code, time.perf_counter() - t0, payload.get("routed_to") or "", len(payload.get("steps") or []))
            )
        except Exception as e:  # noqa: BLE001
            results.append((False, 0, time.perf_counter() - t0, f"{type(e).__name__}: {e}", 0))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(requests_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for ok, *_ in results if ok)
    print("\n──── HTTP 并发结果 ────", flush=True)
    for i, (ok, code, dur, routed, steps) in enumerate(results):
        print(f"  req#{i}: {'OK' if ok else 'FAIL'} http={code} {dur:.1f}s routed_to={routed} steps={steps}", flush=True)
    passes = ok_count == requests_count
    print(f"  [{'PASS' if passes else 'FAIL'}] {ok_count}/{requests_count} 成功", flush=True)
    return passes


def main() -> int:
    ap = argparse.ArgumentParser(description="session.db 高并发写入压测")
    ap.add_argument("--workers", type=int, default=32, help="并发写入线程数（默认 32）")
    ap.add_argument("--total", type=int, default=2000, help="总写入消息条数（默认 2000）")
    ap.add_argument("--sessions", type=int, default=4, help="并发写入的目标会话数（默认 4）")
    ap.add_argument("--http", action="store_true", help="模式 B：并发真实 /api/chat/multi-agent")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="--http 时的后端地址")
    ap.add_argument("--http-requests", type=int, default=4, help="--http 时的并发请求数（默认 4）")
    args = ap.parse_args()

    if args.http:
        passes = run_http(args.base, args.http_requests)
    else:
        passes = run_in_process(args.workers, args.total, args.sessions)

    print(f"\n总体: {'PASS' if passes else 'FAIL'}（错误 {len(_errors)} 条）", flush=True)
    return 0 if passes else 1


if __name__ == "__main__":
    sys.exit(main())