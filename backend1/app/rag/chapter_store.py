import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

from app.storage import backends, schema as storage_schema


class ChapterStore:
    """章节元数据存储（支持 SQLite / MySQL / PostgreSQL）。

    sqlite 路径：本地 .db 文件（WAL + 显式写锁，上传线程与检索线程共享同一连接）。
    非 sqlite 路径：统一后端门面（schema 由 Alembic 迁移链管理），按线程复用连接，
    与 sqlite 的"连接不主动关闭"语义对齐。
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        # 写锁：上传流程（事件循环线程）与检索（executor 线程）共享同一连接，
        # 用显式锁保护写事务，避免并发写入冲突。
        self._write_lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        """获取数据库连接，支持 WAL 模式和超时配置（sqlite）或统一后端门面（非 sqlite）。"""
        if not backends.is_sqlite():
            return backends.thread_local_connect()
        if self._conn is None:
            self._conn = backends.make_sqlite_conn(
                self.db_path,
                wal=True,
                busy_timeout=5000,
            )
        return self._conn

    def _init_db(self):
        """初始化数据库表和索引。"""
        with self._write_lock:
            conn = self._get_conn()
            if backends.is_sqlite():
                conn.executescript(
                    storage_schema.derive_sqlite_ddl(storage_schema.CHAPTER_TABLES)
                )
                conn.commit()
            else:
                # 非 sqlite：表结构由 Alembic 迁移链统一管理（backends.init_schema 惰性兜底）
                backends.init_schema()

    def add_chapter(
        self, document_id: str, filename: str,
        chapter_number: Optional[int], chapter_title: str,
        summary: str, parent_chunk_text: str,
    ) -> str:
        """添加章节记录，返回生成的章节 ID。"""
        chapter_id = str(uuid.uuid4())
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO chapters (id, document_id, document_filename, chapter_number, chapter_title, summary, parent_chunk_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chapter_id, document_id, filename, chapter_number, chapter_title, summary, parent_chunk_text),
            )
            conn.commit()
        return chapter_id

    def find_by_keyword(self, keyword: str) -> list[dict]:
        """按关键词模糊匹配章节标题。"""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM chapters WHERE chapter_title LIKE ? ORDER BY document_id, chapter_number",
            (f"%{keyword}%",),
        )
        rows = cursor.fetchall()
        cols = ["id", "document_id", "document_filename", "chapter_number", "chapter_title", "summary", "parent_chunk_id"]
        return [dict(zip(cols, r)) for r in rows]

    def find_by_keywords(self, keywords: list[str]) -> list[dict]:
        """按多个关键词模糊匹配章节标题（OR 关系）。"""
        if not keywords:
            return []
        conn = self._get_conn()
        conditions = " OR ".join(["chapter_title LIKE ?" for _ in keywords])
        params = [f"%{k}%" for k in keywords]
        cursor = conn.execute(
            f"SELECT * FROM chapters WHERE ({conditions}) ORDER BY document_id, chapter_number",
            params,
        )
        rows = cursor.fetchall()
        cols = ["id", "document_id", "document_filename", "chapter_number", "chapter_title", "summary", "parent_chunk_id"]
        return [dict(zip(cols, r)) for r in rows]

    def find_by_number(self, document_id: Optional[str], chapter_number: int) -> list[dict]:
        """按章节序号查找，可限定文档 ID。"""
        conn = self._get_conn()
        if document_id:
            cursor = conn.execute(
                "SELECT * FROM chapters WHERE document_id = ? AND chapter_number = ?",
                (document_id, chapter_number),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM chapters WHERE chapter_number = ?",
                (chapter_number,),
            )
        rows = cursor.fetchall()
        cols = ["id", "document_id", "document_filename", "chapter_number", "chapter_title", "summary", "parent_chunk_id"]
        return [dict(zip(cols, r)) for r in rows]

    def get_all(self, document_id: Optional[str] = None, limit: int = 500) -> list[dict]:
        """获取所有章节记录，可按文档 ID 过滤。"""
        conn = self._get_conn()
        if document_id:
            cursor = conn.execute(
                "SELECT * FROM chapters WHERE document_id = ? ORDER BY chapter_number LIMIT ?",
                (document_id, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM chapters ORDER BY document_id, chapter_number LIMIT ?",
                (limit,),
            )
        rows = cursor.fetchall()
        cols = ["id", "document_id", "document_filename", "chapter_number", "chapter_title", "summary", "parent_chunk_id"]
        return [dict(zip(cols, r)) for r in rows]

    def delete_by_document(self, document_id: str):
        """删除指定文档的所有章节记录。"""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM chapters WHERE document_id = ?", (document_id,))
            conn.commit()

    def clear_all(self) -> int:
        """清空章节库中所有记录，返回删除数量。"""
        with self._write_lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM chapters")
            conn.commit()
            return cur.rowcount
