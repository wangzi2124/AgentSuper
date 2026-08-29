"""Unit tests for the RAG indexing / retrieval layer.

No real models, no network: embeddings/reranker models are stubbed,
vector store uses a throwaway Chroma persist dir, chapter store uses a
temp SQLite file.
"""
import os
import sys
import threading
from pathlib import Path

import pytest

from app.rag.document_processor import DocumentProcessor
from app.rag.chapter_store import ChapterStore
from app.rag.intent import detect_chapter_intent
from app.rag.bm25_index import BM25Index, _tokenize
from app.rag.vector_store import VectorStore
from app.rag.plugin_bridge import (
    set_retriever, get_retriever, set_vector_store, get_vector_store,
)
from app.rag.retriever import (
    Retriever, _doc_key, _reciprocal_rank_fusion, _dialogue_rrf,
)
from app.storage import file_generator


# ---------------------------------------------------------------------------
# DocumentProcessor
# ---------------------------------------------------------------------------

class TestReadTextFile:
    def test_utf8_sig(self, tmp_path: Path):
        p = tmp_path / "a.txt"
        p.write_bytes("\ufeff你好".encode("utf-8"))
        assert DocumentProcessor()._read_text_file(str(p)) == "你好"

    def test_gbk(self, tmp_path: Path):
        p = tmp_path / "b.txt"
        p.write_bytes("中文内容".encode("gb18030"))
        assert DocumentProcessor()._read_text_file(str(p)) == "中文内容"

    def test_utf8_fallback_ignore(self, tmp_path: Path):
        p = tmp_path / "c.txt"
        p.write_bytes(b"ok \xff\xfe bytes")
        out = DocumentProcessor()._read_text_file(str(p))
        assert "ok" in out

    def test_garbage_control_chars_raises(self, tmp_path: Path):
        p = tmp_path / "d.bin"
        p.write_bytes(bytes([0, 1, 2, 3]) * 10)
        with pytest.raises(ValueError, match="not appear to be a readable text file"):
            DocumentProcessor()._read_text_file(str(p))


class TestLoad:
    def test_txt_and_md(self, tmp_path: Path):
        t = tmp_path / "x.md"
        t.write_text("# hi", encoding="utf-8")
        assert DocumentProcessor().load(str(t)) == "# hi"

    def test_unsupported_ext(self, tmp_path: Path):
        p = tmp_path / "y.docx"
        p.write_bytes(b"")
        with pytest.raises(ValueError, match="Unsupported file type"):
            DocumentProcessor().load(str(p))

    def test_pdf_import_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.setitem(sys.modules, "pypdf", None)
        p = tmp_path / "z.pdf"
        p.write_bytes(b"%PDF-1.4")
        with pytest.raises(ImportError, match="pypdf is required"):
            DocumentProcessor().load(str(p))


class TestSplitChapters:
    def test_no_match(self):
        chunks = DocumentProcessor()._split_chapters("hello world no chapters")
        assert chunks == [("hello world no chapters", "")]

    def test_cn_and_en_boundaries(self):
        text = "第一章 A\n内容一\n第二章 B\n内容二\nChapter 3\n内容三"
        chunks = DocumentProcessor()._split_chapters(text)
        titles = [c[1] for c in chunks]
        assert titles == ["第一章", "第二章", "Chapter 3"]
        assert chunks[0][0].startswith("第一章")
        assert chunks[0][0].endswith("内容一")
        assert chunks[-1][0].endswith("内容三")


class TestParseChapterNumber:
    def test_cn_number(self):
        proc = DocumentProcessor()
        assert proc._parse_chapter_number("第十二章") == (12, "第十二章")
        assert proc._parse_chapter_number("第一百零三章") == (103, "第一百零三章")

    def test_arabic_number(self):
        assert DocumentProcessor()._parse_chapter_number("第20章") == (20, "第20章")

    def test_no_number(self):
        n, raw = DocumentProcessor()._parse_chapter_number("序章")
        assert n == 0 and raw == "序章"


class TestChunkText:
    def test_overlap_and_boundaries(self):
        dp = DocumentProcessor(chunk_size=10, chunk_overlap=4)
        text = "abcdefghijklmnopqrstuvwxyz"
        chunks = dp._chunk_text(text, {"doc": "d"})
        assert chunks[0][0] == "abcdefghij"
        assert chunks[1][0] == "ghijklmnop"
        assert chunks[1][1]["chunk_start"] == 6
        assert chunks[-1][0] == "stuvwxyz"

    def test_empty_text(self):
        assert DocumentProcessor()._chunk_text("", {}) == []


class TestExtractDialogues:
    def test_prefix_and_suffix(self):
        dp = DocumentProcessor()
        text = '张三说：“你好世界”\n李四道：“回家吃饭”\n“他在说：云彩”？\n王五答“明天见”'
        anchors = dp._extract_dialogues(text, {"base": True})
        assert anchors, "expected dialogue anchors"
        assert any('张三说：“你好世界”' == t for t, _ in anchors)
        assert any(a.get("is_dialogue") for _, a in anchors)

    def test_dedupe(self):
        dp = DocumentProcessor()
        text = '张三说：“重复一句”\n张三说：“重复一句”\n'
        anchors = dp._extract_dialogues(text, {})
        first = [a for a in anchors if "重复一句" in a[0]]
        assert len(first) == 1

    def test_no_matches(self):
        assert DocumentProcessor()._extract_dialogues("plain body", {}) == []


class TestProcess:
    def test_process_returns_chunks_and_metas(self, tmp_path: Path):
        p = tmp_path / "doc.md"
        p.write_text(
            "第一章 开始\n" + "字" * 1200 + "\n"
            '小红问：“这本书讲了什么？”\n'
            "第二章 继续\n" + "字" * 300,
            encoding="utf-8",
        )
        chunks, metas = DocumentProcessor(chunk_size=500, chunk_overlap=100).process(str(p), "doc-1", "doc.md")
        assert len(metas) == 2
        assert metas[0]["chapter_number"] == 1
        assert metas[0]["chapter_title"] == "第一章"
        assert any(c[1].get("is_parent") for c in chunks)
        assert any(c[1].get("is_dialogue") for c in chunks)
        ids = {c[1]["document_id"] for c in chunks}
        assert ids == {"doc-1"}

    def test_single_text_block(self, tmp_path: Path):
        p = tmp_path / "plain.txt"
        p.write_text("no chapters here", encoding="utf-8")
        chunks, metas = DocumentProcessor().process(str(p), "d", "plain.txt")
        assert metas[0]["chapter_title"] == ""
        assert metas[0]["chapter_number"] is None


# ---------------------------------------------------------------------------
# ChapterStore
# ---------------------------------------------------------------------------

class TestChapterStore:
    def _store(self, tmp_path: Path) -> ChapterStore:
        return ChapterStore(str(tmp_path / "chapters.db"))

    def test_crud(self, tmp_path: Path):
        cs = self._store(tmp_path)
        cid = cs.add_chapter("doc-a", "a.md", 1, "第一章", "摘要一", "摘要一")
        assert cid
        assert cs.find_by_keyword("第一")[0]["chapter_title"] == "第一章"
        assert len(cs.find_by_keywords(["第一", "不存在"])) == 1
        assert cs.find_by_keywords([]) == []
        assert cs.find_by_number("doc-a", 1)[0]["document_id"] == "doc-a"
        assert cs.find_by_number(None, 1)[0]["chapter_title"] == "第一章"
        assert cs.find_by_number("doc-a", 99) == []
        all_rows = cs.get_all()
        assert len(all_rows) == 1
        assert cs.get_all("doc-a")[0]["document_filename"] == "a.md"

    def test_delete_and_clear(self, tmp_path: Path):
        cs = self._store(tmp_path)
        cs.add_chapter("doc-a", "a.md", 1, "第一章", "s", "s")
        cs.add_chapter("doc-b", "b.md", 1, "第2章", "t", "t")
        cs.delete_by_document("doc-a")
        assert len(cs.get_all()) == 1
        assert cs.clear_all() == 1
        assert cs.get_all() == []


# ---------------------------------------------------------------------------
# intent
# ---------------------------------------------------------------------------

class TestIntent:
    def test_cn_number(self):
        assert detect_chapter_intent("第三章讲了什么") == {"chapter_number": 3, "chapter_title_raw": "第三章"}

    def test_cn_compound(self):
        assert detect_chapter_intent("第二十三章讲的") == {"chapter_number": 23, "chapter_title_raw": "第二十三章"}

    def test_arabic(self):
        assert detect_chapter_intent("第12章在哪？")["chapter_number"] == 12

    def test_english(self):
        assert detect_chapter_intent("Chapter 5 summary")["chapter_number"] == 5
        assert detect_chapter_intent("chapter 9")["chapter_number"] == 9

    def test_keyword_only(self):
        r = detect_chapter_intent("这本书的章节内容是什么")
        assert r and r["chapter_number"] is None

    def test_no_intent(self):
        assert detect_chapter_intent("你好呀") is None


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

class TestBM25:
    def test_build_search_add_remove(self):
        idx = BM25Index()
        docs = ["无关文档 甲", "无关文档 乙", "张三在公园散步", "无关文档 丁", "无关文档 戊"]
        idx.build(docs, [{"document_id": str(i)} for i in range(len(docs))])
        found = idx.search("公园", k=2)
        assert found and "公园" in found[0][0]["text"]

        idx.add(["王五在公园跑步"], [{"document_id": "9"}])
        assert len(idx.documents) == 6
        assert idx.search("跑步")

        idx.remove_by_metadata("document_id", "9")
        assert len(idx.documents) == 5
        assert not idx.search("跑步")

        idx2 = BM25Index()
        assert idx2.search("anything") == []
        idx2.add(["某文档"], [{}])
        assert len(idx2.documents) == 1

    def test_clear(self):
        idx = BM25Index()
        idx.build(["abc def"], [{}])
        idx.clear()
        assert idx.documents == [] and idx.search("abc") == []

    def test_remove_makes_empty(self):
        idx = BM25Index()
        idx.build(["only doc"], [{"id": 1}])
        idx.remove_by_metadata("id", 1)
        assert idx.bm25 is None

    def test_tokenize_fallback(self):
        toks = _tokenize("hello,world。foo")
        assert "foo" in toks


# ---------------------------------------------------------------------------
# VectorStore (real Chroma, temp dir)
# ---------------------------------------------------------------------------

class TestVectorStore:
    def _vs(self, tmp_path: Path) -> VectorStore:
        return VectorStore(str(tmp_path / "chroma"))

    def test_add_search(self, tmp_path: Path):
        vs = self._vs(tmp_path)
        ids = vs.add(["苹果是红色的水果", "香蕉是黄色的水果"],
                     [{"document_id": "d1"}, {"document_id": "d2"}],
                     [[1.0, 0.0], [0.0, 1.0]])
        assert len(ids) == 2
        assert vs.count == 2
        res = vs.similarity_search([1.0, 0.0], k=1)
        assert res and res[0][0]["text"] == "苹果是红色的水果"
        assert res[0][1] > 0

    def test_search_empty(self, tmp_path: Path):
        vs = self._vs(tmp_path)
        assert vs.similarity_search([1.0], k=5) == []

    def test_where_filter(self, tmp_path: Path):
        vs = self._vs(tmp_path)
        vs.add(["a", "b"], [{"kind": "x"}, {"kind": "y"}], [[1.0], [1.0]])
        res = vs.similarity_search([1.0], k=5, where={"kind": "x"})
        assert len(res) == 1 and res[0][0]["metadata"]["kind"] == "x"

    def test_delete_and_clear(self, tmp_path: Path):
        vs = self._vs(tmp_path)
        vs.add(["a", "b"], [{"document_id": "d1"}, {"document_id": "d2"}], [[1.0], [1.0]])
        vs.delete_by_metadata("document_id", "d1")
        assert vs.count == 1
        assert vs.clear_all() == 1
        assert vs.count == 0

    def test_get_all_and_chunks(self, tmp_path: Path):
        vs = self._vs(tmp_path)
        vs.add(["one", "two", "three"],
               [{"document_id": "d1"}, {"document_id": "d1"}, {"document_id": "d2"}],
               [[1.0], [1.0], [1.0]])
        docs, metas = vs.get_all()
        assert len(docs) == 3
        chunks, total = vs.get_chunks(offset=1, limit=10)
        assert total == 3 and len(chunks) == 2
        chunks_d, total_d = vs.get_chunks(document_id="d1", limit=10)
        assert total_d == 2
        chunks_q, total_q = vs.get_chunks(query="tw", limit=10)
        assert chunks_q and "tw" in chunks_q[0]["text"]
        chunks_e, total_e = vs.get_chunks(query="nope", limit=10)
        assert chunks_e == [] and total_e == 0

    def test_load_classmethod(self, tmp_path: Path):
        a = VectorStore(str(tmp_path / "c"))
        a.add(["x"], [{"d": 1}], [[1.0]])
        b = VectorStore.load(str(tmp_path / "c"))
        assert b.count == 1


# ---------------------------------------------------------------------------
# plugin_bridge
# ---------------------------------------------------------------------------

class TestPluginBridge:
    def test_set_get_retriever(self):
        r = object()
        set_retriever(r)
        assert get_retriever() is r
        set_retriever(None)

    def test_get_vector_store_fallback(self):
        class RS:
            vector_store = object()
        rs = RS()
        set_vector_store(None)
        set_retriever(rs)
        assert get_vector_store() is rs.vector_store
        vs = object()
        set_vector_store(vs)
        assert get_vector_store() is vs
        set_retriever(None)
        set_vector_store(None)


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class _FakeEmb:
    def __init__(self, vec):
        self.vec = vec

    def embed_query(self, text):
        return self.vec


class _FakeVS:
    def __init__(self):
        self.calls = []

    @property
    def count(self):
        return 5

    def similarity_search(self, emb, k=5, where=None):
        self.calls.append((emb, k, where))
        return [({"text": "向量命中", "metadata": {"document_id": "a", "chapter_title": "第一章"}}, 0.9)]

    def get_chunks(self, document_id=None, query=None):
        return [{"text": "子块", "metadata": {"document_id": document_id, "chapter_title": query}}], 1


class _FakeChapter:
    def __init__(self, rows=None):
        self.rows = rows or []

    def find_by_number(self, document_id, number):
        return [r for r in self.rows if r["chapter_number"] == number]

    def find_by_keyword(self, keyword):
        return [r for r in self.rows if keyword in r["chapter_title"]]

    def find_by_keywords(self, keywords):
        return [r for r in self.rows if any(k in r["chapter_title"] for k in keywords)]


def _mk_retriever(vs=None, emb=None, bm25=None, ch=None):
    return Retriever(
        vector_store=vs or _FakeVS(),
        embeddings=emb or _FakeEmb([1.0, 0.0]),
        bm25_index=bm25,
        chapter_store=ch,
    )


class TestReciprocalRankFusion:
    def test_fusion_merges_by_key(self):
        d1 = {"text": "t1", "metadata": {"document_id": "a"}}
        d2 = {"text": "t2", "metadata": {"document_id": "b"}}
        out = _reciprocal_rank_fusion([(d1, 1.0)], [(d1, 1.0), (d2, 0.5)])
        assert len(out) == 2
        assert out[0][0]["text"] == "t1"

    def test_doc_key_stable(self):
        a = {"text": "same", "metadata": {"document_id": "x"}}
        b = dict(a)
        assert _doc_key(a) == _doc_key(b)

    def test_dialogue_rrf_empty(self):
        d = [({"text": "solo", "metadata": {}}, 0.5)]
        assert _dialogue_rrf(d, []) is d

    def test_dialogue_rrf_merge(self):
        a = {"text": "main", "metadata": {"document_id": "m"}}
        b = {"text": "dial", "metadata": {"document_id": "d"}}
        out = _dialogue_rrf([(a, 1.0)], [(b, 0.8)])
        assert len(out) == 2


class TestRetrieverInvoke:
    def test_chapter_lookup(self):
        ch = _FakeChapter([{
            "document_id": "a", "document_filename": "a.md",
            "chapter_number": 3, "chapter_title": "第三章", "summary": "第三章内容摘要",
        }])
        r = _mk_retriever(ch=ch)
        res = r.invoke("第三章内容", k=5)
        assert res and res[0][0]["metadata"]["_chapter_lookup"]

    def test_chapter_lookup_failure_falls_back(self):
        class BoomChapter(_FakeChapter):
            def find_by_number(self, *a, **k):
                raise RuntimeError("boom")
        r = _mk_retriever(ch=BoomChapter())
        res = r.invoke("第三章", k=5)
        assert res, "should fall back to hybrid search"

    def test_vector_search_failure_bm25_only(self):
        class BoomVS(_FakeVS):
            def similarity_search(self, emb, k=5, where=None):
                raise RuntimeError("chroma down")
        bm = BM25Index()
        bm.build(["无关文档 甲", "无关文档 乙", "bm25命中文本", "无关文档 丁", "无关文档 戊"],
                 [{"document_id": str(i)} for i in range(5)])
        r = _mk_retriever(vs=BoomVS(), bm25=bm)
        res = r.invoke("bm25", k=5)
        assert res and "bm25" in res[0][0]["text"]

    def test_bm25_failure_uses_vector_only(self):
        class BoomBM25:
            def search(self, q, k=5):
                raise RuntimeError("bm25 down")
        r = _mk_retriever(bm25=BoomBM25())
        res = r.invoke("向量命中")
        assert res and "向量" in res[0][0]["text"]

    def test_enrich_with_parent(self):
        ch = _FakeChapter([{
            "document_id": "a", "document_filename": "a.md", "chapter_number": 1,
            "chapter_title": "第一章", "summary": "第一章摘要",
        }])
        r = _mk_retriever(ch=ch)
        docs = [({"text": "x", "metadata": {"chapter_title": "第一章"}}, 1.0)]
        out = r._enrich_with_parent(docs)
        assert out[0][0]["metadata"]["chapter_summary"] == "第一章摘要"

    def test_chapter_enrichment_failure_ok(self):
        class BoomChapter(_FakeChapter):
            def find_by_keywords(self, *a, **k):
                raise RuntimeError("y")
        r = _mk_retriever(ch=BoomChapter())
        res = r.invoke("向量命中")
        assert res

    def test_is_empty(self):
        r = _mk_retriever()
        assert r.is_empty is False

    def test_no_bm25_uses_vector(self):
        r = _mk_retriever()
        res = r.invoke("x")
        assert len(res) == 5 or res


# ---------------------------------------------------------------------------
# embeddings + reranker (models stubbed)
# ---------------------------------------------------------------------------

class _FakeModel:
    def encode(self, texts, batch_size=None, show_progress_bar=None):
        import numpy as np
        return np.array([[1.0, 2.0, 3.0]] * len(texts))

    def predict(self, pairs):
        import numpy as np
        return np.array([0.9, 0.1, 0.5])


class TestLocalEmbeddings:
    def test_embed_documents_batched(self, monkeypatch):
        from app.rag.embeddings import LocalEmbeddings
        monkeypatch.setattr(LocalEmbeddings, "_load_model", lambda self, name: _FakeModel())
        emb = LocalEmbeddings("fake-model")
        out = emb.embed_documents(["a", "b", "c"], batch_size=2)
        assert len(out) == 3 and len(out[0]) == 3

    def test_embed_documents_progress(self, monkeypatch):
        from app.rag.embeddings import LocalEmbeddings
        monkeypatch.setattr(LocalEmbeddings, "_load_model", lambda self, name: _FakeModel())
        emb = LocalEmbeddings("fake-model")
        seen = []
        emb.embed_documents(["a", "b", "c"], batch_size=2, on_progress=lambda d, t: seen.append((d, t)))
        assert seen == [(2, 3), (3, 3)]

    def test_embed_documents_empty(self, monkeypatch):
        from app.rag.embeddings import LocalEmbeddings
        monkeypatch.setattr(LocalEmbeddings, "_load_model", lambda self, name: _FakeModel())
        assert LocalEmbeddings("fake-model").embed_documents([]) == []

    def test_embed_query(self, monkeypatch):
        from app.rag.embeddings import LocalEmbeddings
        monkeypatch.setattr(LocalEmbeddings, "_load_model", lambda self, name: _FakeModel())
        out = LocalEmbeddings("fake-model").embed_query("q")
        assert out == [1.0, 2.0, 3.0]

    def test_resolve_local_model(self, monkeypatch, tmp_path: Path):
        from app.rag.embeddings import LocalEmbeddings
        monkeypatch.setattr(LocalEmbeddings, "_load_model", lambda self, name: _FakeModel())
        emb = LocalEmbeddings("fake-model")
        cache = tmp_path / "cache"
        (cache / "mymodel").mkdir(parents=True)
        emb.local_cache_dir = cache
        assert emb._resolve_local_model("mymodel") == (cache / "mymodel").resolve()
        assert emb._resolve_local_model("bogus") is None


class TestReranker:
    def _make(self, monkeypatch, model=None):
        from app.rag.reranker import Reranker
        monkeypatch.setattr(Reranker, "_load_model", lambda self, name: model or _FakeModel())
        return Reranker("cross-encoder/fake")

    def test_rerank_orders(self, monkeypatch):
        rr = self._make(monkeypatch)
        docs = [{"content": "c低"}, {"content": "c高"}]
        import numpy as np

        class M:
            def predict(self, pairs):
                return np.array([0.1, 0.9])
        monkeypatch.setattr(type(rr.model), "predict", M().predict)
        out = rr.rerank("q", docs, top_k=3)
        assert out[0][0] == docs[1]

    def test_rerank_degrades_on_predict_error(self, monkeypatch):
        rr = self._make(monkeypatch)

        class M:
            def predict(self, pairs):
                raise RuntimeError("model failed")
        monkeypatch.setattr(type(rr.model), "predict", M().predict)
        docs = [{"content": "a"}, {"content": "b"}]
        out = rr.rerank("q", docs)
        assert [(d, 0.0) for d in docs] == out

    def test_rerank_empty(self, monkeypatch):
        assert self._make(monkeypatch).rerank("q", []) == []

    def test_resolve_local_model(self, monkeypatch, tmp_path: Path):
        from app.rag.reranker import Reranker
        monkeypatch.setattr(Reranker, "_load_model", lambda self, name: _FakeModel())
        rr = Reranker("x", cache_dir=tmp_path)
        (tmp_path / "x").mkdir(parents=True)
        assert rr._resolve_local_model("x") == (tmp_path / "x").resolve()
        assert rr._resolve_local_model("missing") is None


# ---------------------------------------------------------------------------
# file_generator
# ---------------------------------------------------------------------------

class TestFileGenerator:
    def _monkeypatch_dir(self, monkeypatch, tmp_path: Path):
        d = tmp_path / "generated"
        monkeypatch.setattr(file_generator, "GENERATED_DIR", d)
        return d

    def test_save_text_and_bytes(self, monkeypatch, tmp_path: Path):
        d = self._monkeypatch_dir(monkeypatch, tmp_path)
        p1 = file_generator.save_file("hello!", "out.txt")
        assert Path(p1).read_text(encoding="utf-8") == "hello!"
        p2 = file_generator.save_file(b"\x00\x01", "bin.dat")
        assert Path(p2).read_bytes() == b"\x00\x01"

    def test_save_generated_name(self, monkeypatch, tmp_path: Path):
        self._monkeypatch_dir(monkeypatch, tmp_path)
        p = Path(file_generator.save_file("x"))
        assert p.name.startswith("file_20")

    def test_save_strips_traversal(self, monkeypatch, tmp_path: Path):
        d = self._monkeypatch_dir(monkeypatch, tmp_path)
        p = Path(file_generator.save_file("x", "../../evil.txt"))
        assert p == (d / "evil.txt")

    def test_save_dot_names_fallback(self, monkeypatch, tmp_path: Path):
        self._monkeypatch_dir(monkeypatch, tmp_path)
        p = Path(file_generator.save_file("x", "."))
        assert p.name.startswith("file_20")

    def test_subdir_name_basename(self, monkeypatch, tmp_path: Path):
        self._monkeypatch_dir(monkeypatch, tmp_path)
        p = Path(file_generator.save_file("x", "sub/ok.txt"))
        assert p.name == "ok.txt"