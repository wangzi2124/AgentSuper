import sqlite3
import uuid
from pathlib import Path
from typing import Optional


class ChapterStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                document_filename TEXT NOT NULL,
                chapter_number INTEGER,
                chapter_title TEXT NOT NULL,
                summary TEXT NOT NULL,
                parent_chunk_id TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chapters_doc
            ON chapters(document_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chapters_title
            ON chapters(chapter_title)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chapters_number
            ON chapters(chapter_number)
        """)
        conn.commit()

    def add_chapter(
        self, document_id: str, filename: str,
        chapter_number: Optional[int], chapter_title: str,
        summary: str, parent_chunk_text: str,
    ) -> str:
        chapter_id = str(uuid.uuid4())
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO chapters (id, document_id, document_filename, chapter_number, chapter_title, summary, parent_chunk_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chapter_id, document_id, filename, chapter_number, chapter_title, summary, parent_chunk_text),
        )
        conn.commit()
        return chapter_id

    def find_by_keyword(self, keyword: str) -> list[dict]:
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM chapters WHERE chapter_title LIKE ? ORDER BY document_id, chapter_number",
            (f"%{keyword}%",),
        )
        rows = cursor.fetchall()
        cols = ["id", "document_id", "document_filename", "chapter_number", "chapter_title", "summary", "parent_chunk_id"]
        return [dict(zip(cols, r)) for r in rows]

    def find_by_number(self, document_id: Optional[str], chapter_number: int) -> list[dict]:
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
        conn = self._get_conn()
        conn.execute("DELETE FROM chapters WHERE document_id = ?", (document_id,))
        conn.commit()
