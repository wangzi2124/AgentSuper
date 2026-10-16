# -*- coding: utf-8 -*-
"""D2 跨库一致性：FileStore index_state 追踪 + 自愈重放测试。

覆盖：
  - save() 初始化 index_state=pending / chunk_count=0；mark_index_state 切换 ready/failed
  - _collect_incomplete 识别 ready 之外 + 无该字段的旧数据
  - _index_one 从主库文件重放建索引（写前按 document_id 幂等清理），成功后标记 ready
  - 建索引异常 → 标记 failed 并保留错误，不吞孤儿数据
运行：pytest tests/test_kb_repair.py
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.storage.file_store import FileStore
from app.services.kb_repair import _collect_incomplete, _index_one


class _StubVS:
    """向量库桩：记录 add/delete 调用。"""

    def __init__(self):
        self.added = []
        self.deleted = []

    def add(self, texts, metadatas, embeddings):
        self.added.append((list(texts), list(metadatas), list(embeddings)))

    def delete_by_metadata(self, key, value):
        self.deleted.append((key, value))


class _StubBM25:
    def __init__(self):
        self.added = []
        self.removed = []

    def add(self, texts, metadatas):
        self.added.append((list(texts), list(metadatas)))

    def remove_by_metadata(self, key, value):
        self.removed.append((key, value))


class _StubProc:
    def __init__(self, chunks=None, chapters=None, error=None):
        self.chunks = chunks or [("hello world", {"document_id": "x"})]
        self.chapters = chapters or []
        self.error = error

    def process(self, file_path, doc_id, filename):
        if self.error:
            raise self.error
        metas = [dict(m, document_id=doc_id) for (_, m) in self.chunks]
        return [(t, m) for (t, _), m in zip(self.chunks, metas)], self.chapters


class _StubEmb:
    def __init__(self):
        self.count = 0

    def embed_documents(self, texts, batch, cb):
        self.count += 1
        return [[0.1, 0.2, 0.3]] * len(texts)


def _app_state(tmp_path, proc):
    fs = FileStore(str(tmp_path / "uploads"))
    vs, bm25, emb = _StubVS(), _StubBM25(), _StubEmb()
    app_state = type("State", (), {
        "file_store": fs, "vector_store": vs, "bm25_index": bm25,
        "embeddings": emb, "doc_processor": proc, "chapter_store": None,
    })()
    return fs, vs, bm25, app_state


def _doc(fs, doc_id):
    """生产路径经 fs.list_all() 注入 id 键；测试用 helper 等价拼装。"""
    return {"id": doc_id, **fs.get(doc_id)}


def test_save_initializes_index_state(tmp_path):
    fs = FileStore(str(tmp_path / "uploads"))
    doc_id, _ = fs.save("a.txt", b"hello world")
    meta = fs.get(doc_id)
    assert meta["index_state"] == "pending"
    assert meta["chunk_count"] == 0
    assert meta["index_error"] is None

    fs.mark_index_state(doc_id, "ready", chunk_count=5)
    assert fs.get(doc_id)["index_state"] == "ready"
    assert fs.get(doc_id)["chunk_count"] == 5

    fs.mark_index_state(doc_id, "failed", error="embed timeout")
    assert fs.get(doc_id)["index_state"] == "failed"
    assert fs.get(doc_id)["index_error"] == "embed timeout"


def test_collect_incomplete_skips_ready(tmp_path):
    fs = FileStore(str(tmp_path / "uploads"))
    d1, _ = fs.save("ok.txt", b"1")
    d2, _ = fs.save("bad.txt", b"2")
    d3, _ = fs.save("legacy.txt", b"3")
    fs.mark_index_state(d1, "ready")
    fs.mark_index_state(d2, "failed", error="boom")
    # 旧数据（无 index_state 字段）也应识为待修复
    fs.metadata[d3].pop("index_state")
    fs._save_metadata()

    app_state = type("S", (), {"file_store": fs})()
    ids = {d["id"] for d in _collect_incomplete(app_state)}
    assert ids == {d2, d3}


def test_repair_reindexes_and_marks_ready(tmp_path):
    fs, vs, bm25, app_state = _app_state(tmp_path, _StubProc())
    doc_id, path = fs.save("doc.txt", b"some content")
    fs.mark_index_state(doc_id, "failed", error="stale crash")

    err = _index_one(app_state, _doc(fs, doc_id))
    assert err == ""
    assert fs.get(doc_id)["index_state"] == "ready"
    assert fs.get(doc_id)["chunk_count"] == 1
    assert fs.get(doc_id)["index_error"] is None
    # 写前幂等清理 + 写入派生库
    assert vs.deleted == [("document_id", doc_id)]
    assert vs.added and vs.added[0][0] == ["hello world"]
    assert vs.added[0][1][0]["document_id"] == doc_id
    assert bm25.removed == [("document_id", doc_id)]
    assert bm25.added[0][0] == ["hello world"]


def test_repair_cleans_before_readd_so_idempotent(tmp_path):
    """重复自愈不产生重复分块：每次重放前都会清掉上个半成品。"""
    fs, vs, bm25, app_state = _app_state(tmp_path, _StubProc())
    doc_id, _ = fs.save("doc.txt", b"x")
    fs.mark_index_state(doc_id, "failed", error="x")

    assert _index_one(app_state, _doc(fs, doc_id)) == ""
    assert _index_one(app_state, _doc(fs, doc_id)) == ""
    # 两次自愈：delete_by_metadata 各触发一次，add 每次重建
    assert len(vs.deleted) == 2
    assert len(vs.added) == 2


def test_repair_failure_marks_failed_keeps_error(tmp_path):
    fs, vs, bm25, app_state = _app_state(
        tmp_path, _StubProc(error=RuntimeError("no text box parser"))
    )
    doc_id, _ = fs.save("doc.txt", b"bad")
    fs.mark_index_state(doc_id, "processing")

    err = _index_one(app_state, _doc(fs, doc_id))
    assert err != ""
    assert fs.get(doc_id)["index_state"] == "failed"
    assert "no text box parser" in fs.get(doc_id)["index_error"]


def test_repair_missing_source_marks_failed(tmp_path):
    fs, _, _, app_state = _app_state(tmp_path, _StubProc())
    doc_id, path = fs.save("gone.txt", b"1")
    os.remove(path)  # 源文件丢失
    err = _index_one(app_state, _doc(fs, doc_id))
    assert err != ""
    assert fs.get(doc_id)["index_state"] == "failed"
    assert "源文件缺失" in fs.get(doc_id)["index_error"]