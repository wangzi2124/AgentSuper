# -*- coding: utf-8 -*-
"""统一数据库后端（MySQL / PostgreSQL）端到端验证脚本。

针对四个 SQLite 子系统验证其在目标数据库上的真实行为：
  1. 建库（不存在时自动创建 `agentsuper`）
  2. Alembic 迁移链 upgrade head（跨 4 个子系统全部表）
  3. session.repository ／ catalog_db ／ chapter_store ／ task_state 冒烟回环

用法（在 backend/ 下，需本机 MySQL/PG 在跑且凭据正确）：
  .venv\\Scripts\\python.exe -X utf8 scripts/verify_backend_db.py mysql
  .venv\\Scripts\\python.exe -X utf8 scripts/verify_backend_db.py postgresql

凭据从 backend/.env 的 DB_* 或下方默认读取（与 app.config.Settings 默认一致）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.storage import backends, schema as storage_schema

_FAILED = []


def check(step: str, cond) -> None:
    tag = "ok  " if cond else "FAIL"
    print(f"  [{tag}] {step}")
    if not cond:
        _FAILED.append(step)


def create_database(kind: str, host: str, port: int, user: str, password: str, dbname: str) -> None:
    if kind == "mysql":
        import pymysql
        conn = pymysql.connect(host=host, port=port, user=user, password=password, connect_timeout=8)
        cur = conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        conn.commit()
        conn.close()
    elif kind == "postgresql":
        import psycopg2
        conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{dbname}"')
        conn.close()
    else:
        raise SystemExit(f"unknown db type: {kind}")


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "mysql"
    if kind not in ("mysql", "postgresql"):
        raise SystemExit("usage: verify_backend_db.py mysql|postgresql")

    settings.db_type = kind
    settings.db_url = None
    host = settings.db_host
    port = settings.db_port if settings.db_port else (3306 if kind == "mysql" else 5432)
    user = settings.db_username
    password = settings.db_password
    dbname = settings.db_name

    print(f"== [{kind}] host={host} port={port} db={dbname} user={user}")
    create_database(kind, host, port, user, password, dbname)
    print(f"== database `{dbname}` ready")

    url = backends.database_url()
    check("database_url 拼装", url.startswith(
        "mysql+pymysql://" if kind == "mysql" else "postgresql+psycopg2://"))

    # 1) Alembic 迁移链
    from app.storage.migrations import run_migrations
    check("run_migrations() 返回 True（非 sqlite）", run_migrations() is True)

    conn = backends.connect()
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT TABLE_NAME AS name FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE()"
        )} if kind == "mysql" else {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )}
    finally:
        conn.close()
    expected = {
        "alembic_version", "projects", "workspaces", "sessions", "session_messages",
        "message_parts", "session_context_epoch", "session_inputs", "session_tasks",
        "catalog_settings", "providers", "catalog_entries", "chapters", "tasks",
    }
    check(f"迁移建出全部表（缺：{expected - tables or '无'}）", expected <= tables)

    # 2) session / repository 回环
    import app.session.repository as repo

    proj = repo.resolve_project("/vbt", name="vbt")
    check("resolve_project upsert", proj.id)

    ses = repo.create_session("u1", proj.id, "/vbt/work")
    check("create_session", bool(ses.id))
    check("kind 默认 multi-agent", ses.kind == "multi-agent")

    m1 = repo.append_message(ses.id, "user", {"role": "user", "content": "你好"})
    check("append_message seq=1", m1.seq == 1)
    m2 = repo.append_message(ses.id, "assistant", {"role": "assistant", "content": "你好！"})
    check("append_message seq=2（MAX+1 原子）", m2.seq == 2)

    p = repo.append_part(ses.id, m1.id, "text", {"text": "你好"})
    check("append_part", bool(p.id))

    msgs = repo.list_messages(ses.id, after_seq=0)
    check("list_messages 2 条", [m.seq for m in msgs] == [1, 2])
    check("list_messages after_seq=1 只返 seq2", [m.seq for m in repo.list_messages(ses.id, after_seq=1)] == [2])
    check("list_parts 1 个", len(repo.list_parts(m1.id)) == 1)

    repo.update_session(ses.id, title="vbt-title")
    check("update_session title", repo.get_session(ses.id).title == "vbt-title")

    repo.add_session_usage(ses.id, 100, 200)
    got = repo.get_session(ses.id)
    check("add_session_usage tokens", got.tokens_input == 100 and got.tokens_output == 200)

    # revert（回到 m1，删 m2 及其 part 需要 cascade）
    repo.revert_to_message(ses.id, m1.id)
    check("revert_to_message 只剩 1 条", [m.seq for m in repo.list_messages(ses.id)] == [1])

    # 3) catalog_db 配置回环（model_catalog.db）
    import app.models.catalog_db as catalog

    catalog.import_config({
        "default_model": "deepseek/deepseek-v4-flash",
        "small_model": "local/olmo-3:7b",
        "providers": {
            "deepseek": {
                "label": "DeepSeek", "api_base": "https://api.deepseek.com",
                "api_key": "k", "enabled": True,
            }
        },
    })
    got = catalog.load_config()
    check("catalog default_model 往返", got.get("default_model") == "deepseek/deepseek-v4-flash")
    check("catalog providers 往返", got.get("providers", {}).get("deepseek", {}).get("api_base") == "https://api.deepseek.com")
    merged = catalog.import_config({**got, "image_caption_model": "openai/gpt-4o-mini"})
    check("import_config 返回合并值", merged.get("image_caption_model") == "openai/gpt-4o-mini")

    # 4) chapter_store 回环（chapter_store.db）
    from app.rag.chapter_store import ChapterStore

    cs = ChapterStore("data/chapter_store.db")
    cid = cs.add_chapter("doc-1", "asdf.md", 1, "第一章 总览", "summary", "chunk-text")
    rows = cs.find_by_keyword("第")
    check("chapter find_by_keyword", any(r["id"] == cid for r in rows))
    check("chapter get_all", [r["id"] for r in cs.get_all("doc-1")] == [cid])
    cs.delete_by_document("doc-1")
    check("chapter delete_by_document", cs.get_all("doc-1") == [])

    # 5) task_state 回环（tasks.db）
    import app.context.task_state as ts
    from app.context.task_state import TaskState

    t = TaskState(conversation_id="conv-vbt")
    t.save()
    t.increment_step()
    t.add_tokens(50)
    st = ts.TaskState.load(t.task_id)
    check("task load", st is not None and st.step == 1 and st.total_tokens == 50)
    check("task list_by_conversation", len(ts.TaskState.list_by_conversation("conv-vbt")) >= 1)
    check("task upsert 仍单条", len([x for x in ts.TaskState.list_by_conversation("conv-vbt") if x.task_id == t.task_id]) == 1)

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    conn = ts._get_db()
    conn.execute(
        "INSERT INTO tasks (id, conversation_id, status, step, total_tokens, "
        "last_compaction_step, tool_calls_count, created_at, updated_at) "
        "VALUES (?, ?, 'completed', 0, 0, 0, 0, ?, ?)",
        ("task_old_vbt", "conv_old", old, old),
    )
    conn.commit()
    ts.cleanup_old_tasks()
    check("cleanup_old_tasks 删除 30 天前旧任务", ts.TaskState.load("task_old_vbt") is None)

    print("== 清理验证会话数据 ==")
    repo.remove_session(ses.id)
    conn2 = backends.connect()
    try:
        for del_, ident in [("DELETE FROM tasks WHERE conversation_id = ?", ("conv-vbt",)),
                            ("DELETE FROM chapters", ()),
                            ("DELETE FROM providers", ()),
                            ("DELETE FROM catalog_settings", ()),
                            ("DELETE FROM catalog_entries", ()),
                            ("DELETE FROM projects WHERE root = ?", ("/vbt",))]:
            conn2.execute(del_, ident)
        conn2.commit()
    finally:
        conn2.close()

    if _FAILED:
        print(f"\nFAILED {len(_FAILED)}: {_FAILED}")
        raise SystemExit(1)
    print(f"\n[{kind}] 全部通过 ✔")
    print(f"  {storage_schema.metadata.tables.keys().__len__()} tables in metadata, done.")


if __name__ == "__main__":
    main()