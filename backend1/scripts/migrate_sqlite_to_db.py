# -*- coding: utf-8 -*-
"""一次性迁移：把旧 sqlite 子库的历史数据导入当前非-sqlite 后端（MySQL/PostgreSQL）。

背景：多子系统后端统一（session.db / model_catalog.db / chapter_store.db / tasks.db
→ SQLAlchemy 单一后端）后，切换 DB_TYPE/DB_URL 只影响新写入；旧的 sqlite 数据不会被
自动搬走。本脚本把存量数据按表逐行复制到当前配置的目标库（幂等：已存在的主键跳过，
重复运行安全）。

用法：
  cd backend
  .venv\\Scripts\\python.exe -X utf8 scripts\\migrate_sqlite_to_db.py           # 复制全部
  .venv\\Scripts\\python.exe -X utf8 scripts\\migrate_sqlite_to_db.py --dry-run  # 只预览
  .venv\\Scripts\\python.exe -X utf8 scripts\\migrate_sqlite_to_db.py --only sessions catalog

sqlite 文件路径默认取 data/{session,model_catalog,chapter_store}.db 与 data/tasks.db，
与旧实现一致。目标库为数据库后端；DB_TYPE=sqlite 时脚本拒绝执行。
"""

import argparse
import sqlalchemy
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.storage.backends import get_engine  # noqa: E402

# 每个旧 sqlite 文件 -> 需迁移的表（目标库同名列结构）
SOURCES = {
    "session": {
        "file": "data/session.db",
        "tables": [
            "projects",
            "workspaces",
            "sessions",
            "session_messages",
            "message_parts",
            "session_context_epoch",
            "session_inputs",
            "session_tasks",
        ],
    },
    "catalog": {
        "file": "data/model_catalog.db",
        "tables": ["catalog_settings", "providers", "catalog_entries"],
    },
    "chapter": {
        "file": "data/chapter_store.db",
        "tables": ["chapters"],
    },
    "tasks": {
        "file": "data/tasks.db",
        "tables": ["tasks"],
    },
}


def quote_ident(name: str) -> str:
    """目标方言标识符引用（MySQL 反引号 / PG 双引号）。"""
    return "`%s`" % name if settings.db_type == "mysql" else '"%s"' % name


def target_engine():
    if settings.db_type == "sqlite":
        sys.exit("DB_TYPE=sqlite：目标库就是 sqlite，本脚本只用于迁移到 MySQL/PostgreSQL")
    return get_engine()


def sqlite_conn(path: Path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--only",
        nargs="*",
        default=list(SOURCES),
        choices=list(SOURCES),
        help="只迁移指定数据集：session / catalog / chapter / tasks",
    )
    args = ap.parse_args()

    engine = target_engine()
    insp = sqlalchemy.inspect(engine)

    total_ins = total_skip = 0
    with engine.begin() as tconn:
        for name in args.only:
            src = SOURCES[name]
            path = Path(src["file"])
            if not path.exists() or path.stat().st_size == 0:
                print("[%s] %s 不存在或为空，跳过" % (name, path))
                continue
            sconn = sqlite_conn(path)
            for table in src["tables"]:
                try:
                    s_cols = [r[1] for r in sconn.execute("PRAGMA table_info(%s)" % table)]
                except sqlite3.Error:
                    continue
                if not s_cols:
                    continue
                if not insp.has_table(table):
                    print("  [%s] 目标库无表 %s，跳过" % (name, table))
                    continue
                pk = insp.get_pk_constraint(table).get("constrained_columns") or []
                d_cols = [c["name"] for c in insp.get_columns(table)]
                cols = [c for c in s_cols if c in d_cols]
                if not cols:
                    continue
                rows = sconn.execute("SELECT * FROM %s" % table).fetchall()
                if not rows:
                    continue
                insertable = [dict(r) for r in rows]
                col_sql = ", ".join(quote_ident(c) for c in cols)
                ph = ", ".join(":%s" % c for c in cols)
                sql = "INSERT INTO %s (%s) VALUES (%s)" % (
                    quote_ident(table), col_sql, ph,
                )
                if pk:
                    pk_where = " AND ".join("%s=:%s" % (quote_ident(c), c) for c in pk)
                    sel = sqlalchemy.text(
                        "SELECT 1 FROM %s WHERE %s LIMIT 1" % (quote_ident(table), pk_where)
                    )

                inserted = skipped = 0
                for row in insertable:
                    if pk:
                        exists = tconn.execute(sel, {c: row[c] for c in pk}).scalar()
                        if exists:
                            skipped += 1
                            continue
                    if not args.dry_run:
                        tconn.execute(sqlalchemy.text(sql), row)
                    inserted += 1
                print(
                    "[%s] %s: 源 %d 行 -> 导入 %d%s%s"
                    % (
                        name,
                        table,
                        len(rows),
                        inserted,
                        "（dry-run 仅预览）" if args.dry_run else "",
                        "（跳过已存在 %d）" % skipped if skipped else "",
                    )
                )
                total_ins += inserted
                total_skip += skipped
            sconn.close()
    print("DONE: 计划导入 %d 行%s" % (total_ins, "（dry-run，未写库）" if args.dry_run else "，已写库"))


if __name__ == "__main__":
    main()