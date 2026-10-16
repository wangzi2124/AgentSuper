# -*- coding: utf-8 -*-
"""kb_repair.py 剩余分支用例（补 test_kb_repair.py）：章节重建、预清理失败容忍、
空文本失败、repair_incomplete_documents 全流程。

运行：pytest tests/test_kb_repair_extra.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.storage.file_store import FileStore
from app.services import kb_repair


class _StubCS:
    def __init__(self):
        self.chapters = []
        self.deleted = []

    def add_chapter(self, **kw):
        self.chapters.append(kw)

    def delete_by_document(self, doc_id):
        self.deleted.append(doc_id)


class _StubProc:
    def __init__(self, chunks=None, chapters=None, error=None):
        self.chunks = chunks if chunks is not None else [("hello world", {"document_id": "x"})]
        self.chapters = chapters or []
        self.error = error

    def process(self, file_path, doc_id, filename):
        if self.error:
            raise self.error
        metas = [dict(m, document_id=doc_id) for (_, m) in self.chunks]
        return [(t, m) for (t, _), m in zip(self.chunks, metas)], self.chapters


class _StubVS:
    def __init__(self, fail=False):
        self.fail = fail
        self.added = []

    def add(self, texts, metadatas, embeddings):
        self.added.append((texts, metadatas, embeddings))

    def delete_by_metadata(self, key, value):
        if self.fail:
            raise RuntimeError("vector down")


class _StubBM25:
    def __init__(self, fail=False):
        self.fail = fail

    def add(self, texts, metadatas):
        pass

    def remove_by_metadata(self, key, value):
        if self.fail:
            raise RuntimeError("bm25 down")


class _StubEmb:
    def embed_documents(self, texts, batch, cb):
        return [[0.1] * len(texts[0])] * len(texts) if texts else []


def _app_state(tmp_path, proc, vs=None, bm25=None, cs=None):
    fs = FileStore(str(tmp_path / "uploads"))
    vs = vs or _StubVS()
    bm25 = bm25 or _StubBM25()
    app_state = type("State", (), {
        "file_store": fs, "vector_store": vs, "bm25_index": bm25,
        "embeddings": _StubEmb(), "doc_processor": proc, "chapter_store": cs,
    })()
    return fs, vs, bm25, app_state


def _doc(fs, doc_id):
    return {"id": doc_id, **fs.get(doc_id)}


def test_index_one_with_chapters(tmp_path):
    cs = _StubCS()
    fs, vs, bm25, app_state = _app_state(tmp_path, _StubProc(
        chapters=[{
            "document_id": "d1", "filename": "a.txt", "chapter_number": 1,
            "chapter_title": "第一章", "summary": "概述", "parent_chunk_text": "文本",
        }],
    ), cs=cs)
    doc_id, _ = fs.save("a.txt", b"x")
    fs.mark_index_state(doc_id, "failed", error="x")
    assert kb_repair._index_one(app_state, _doc(fs, doc_id)) == ""
    assert len(cs.chapters) == 1
    assert cs.chapters[0]["chapter_title"] == "第一章"


def test_index_one_preclean_failures_tolerated(tmp_path):
    fs, vs, bm25, app_state = _app_state(
        tmp_path, _StubProc(), vs=_StubVS(fail=True), bm25=_StubBM25(fail=True),
    )
    doc_id, _ = fs.save("a.txt", b"x")
    fs.mark_index_state(doc_id, "failed", error="x")
    err = kb_repair._index_one(app_state, _doc(fs, doc_id))
    assert err == ""  # 预清理失败不阻断重建
    assert fs.get(doc_id)["index_state"] == "ready"


def test_index_one_empty_text_fails(tmp_path):
    fs, _, _, app_state = _app_state(tmp_path, _StubProc(chunks=[]))
    doc_id, _ = fs.save("empty.txt", b"")
    err = kb_repair._index_one(app_state, _doc(fs, doc_id))
    assert "No text content" in err
    assert fs.get(doc_id)["index_state"] == "failed"


@pytest.mark.asyncio
async def test_repair_incomplete_documents_flow(tmp_path):
    fs, _, _, app_state = _app_state(tmp_path, _StubProc())
    d1, _ = fs.save("ok.txt", b"content1")
    fs.mark_index_state(d1, "failed", error="old crash")
    d2, _ = fs.save("bad.txt", b"bad")
    fs.mark_index_state(d2, "failed", error="x")

    def bad_proc(file_path, doc_id, filename):
        if "bad" in filename:
            raise RuntimeError("parse fail")
        return [("text", {"document_id": doc_id})], []
    app_state.doc_processor = SimpleNamespace(process=bad_proc)

    result = await kb_repair.repair_incomplete_documents(app_state)
    assert result["repaired"] == [d1]
    assert result["failed"] and result["failed"][0]["id"] == d2
    assert result["skipped"] == 1  # 未成功数 = failed
    assert fs.get(d1)["index_state"] == "ready"
    assert fs.get(d2)["index_state"] == "failed"


@pytest.mark.asyncio
async def test_repair_incomplete_documents_empty(tmp_path):
    fs = FileStore(str(tmp_path / "uploads"))
    app_state = type("S", (), {"file_store": fs})()
    result = await kb_repair.repair_incomplete_documents(app_state)
    assert result == {"repaired": [], "failed": [], "skipped": 0}