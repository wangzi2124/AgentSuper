# -*- coding: utf-8 -*-
"""rag/embeddings.py + rag/reranker.py 剩余分支用例（mock SentenceTransformer/
CrossEncoder/download_model，不加载真实模型）。

覆盖：
  - LocalEmbeddings：_resolve_local_model 候选/规范化/rglob、_load_model 本地/
    下载/失败回退本地/双失败 RuntimeError、embed_documents 分块与进度、embed_query
  - Reranker：_resolve_local_model 候选/modelscope rglob、_load_model 本地/下载/
    失败 RuntimeError、rerank 空/预测失败降级/排序 top_k
运行：pytest tests/test_rag_embeddings_reranker.py
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.rag.embeddings import LocalEmbeddings
from app.rag.reranker import Reranker


# ── LocalEmbeddings ────────────────────────────────────────────────────────

class FakeST:
    def __init__(self, *a, **k):
        self.args = a
        self.kwargs = k

    def encode(self, texts, **k):
        import numpy as np
        return np.array([[1.0, 0.0] for _ in texts])


def _emb(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rag.embeddings.SentenceTransformer", FakeST)
    e = LocalEmbeddings.__new__(LocalEmbeddings)
    e.model_name = "m"
    e.backend_dir = tmp_path / "backend"
    e.local_cache_dir = e.backend_dir / "data" / "models"
    return e


def test_resolve_local_model_absolute(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    p = tmp_path / "abs"
    p.mkdir()
    assert e._resolve_local_model(str(p)) == p.resolve()


def test_resolve_local_model_candidates(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    cand = e.backend_dir / "m"
    cand.mkdir(parents=True)
    assert e._resolve_local_model("m") == cand.resolve()


def test_resolve_local_model_normalized_rglob(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    norm = e.local_cache_dir / "bge_small"
    norm.mkdir(parents=True)
    assert e._resolve_local_model("bge/small") == norm.resolve()
    # rglob 深层匹配
    deep = e.local_cache_dir / "x" / "y" / "deepmodel"
    deep.mkdir(parents=True)
    assert e._resolve_local_model("deepmodel") == deep.resolve()
    # 无 → None
    assert e._resolve_local_model("missing") is None


def test_load_model_local(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    p = tmp_path / "model"
    p.mkdir()
    monkeypatch.setattr(e, "_resolve_local_model", lambda n: p)
    m = e._load_model("m")
    assert isinstance(m, FakeST)
    assert m.kwargs["local_files_only"] is True
    assert m.args[0] == str(p)


def test_load_model_download(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    monkeypatch.setattr(e, "_resolve_local_model", lambda n: None)
    monkeypatch.setattr("app.rag.embeddings.download_model", lambda n, d: dl)
    m = e._load_model("m")
    assert isinstance(m, FakeST)


def test_load_model_download_fail_local_fallback(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(e, "_resolve_local_model", lambda n: local)

    def boom(n, d):
        raise RuntimeError("network down")
    monkeypatch.setattr("app.rag.embeddings.download_model", boom)
    m = e._load_model("m")
    assert isinstance(m, FakeST)


def test_load_model_both_fail_raises(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    monkeypatch.setattr(e, "_resolve_local_model", lambda n: None)

    def boom(n, d):
        raise RuntimeError("network down")
    monkeypatch.setattr("app.rag.embeddings.download_model", boom)
    with pytest.raises(RuntimeError, match="Failed to load embedding model"):
        e._load_model("m")


def test_embed_documents_batching(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    e.model = FakeST()
    progress = []
    out = e.embed_documents(["a", "b", "c", "d", "e"], batch_size=2, on_progress=lambda d, t: progress.append((d, t)))
    assert len(out) == 5
    assert all(len(v) == 2 for v in out)
    assert progress == [(2, 5), (4, 5), (5, 5)]
    assert e.embed_documents([]) == []


def test_embed_query(monkeypatch, tmp_path):
    e = _emb(monkeypatch, tmp_path)
    e.model = FakeST()
    assert e.embed_query("q") == [1.0, 0.0]


# ── Reranker ───────────────────────────────────────────────────────────────

class FakeCE:
    def __init__(self, *a, **k):
        self.args = a

    def predict(self, pairs):
        return [1.0, 0.5, 2.0]


def _rk(monkeypatch, tmp_path):
    monkeypatch.setattr("app.rag.reranker.CrossEncoder", FakeCE)
    r = Reranker.__new__(Reranker)
    r.model_name = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    r.backend_dir = tmp_path / "backend"
    r.cache_dir = tmp_path / "models"
    return r


def test_reranker_resolve_local(monkeypatch, tmp_path):
    r = _rk(monkeypatch, tmp_path)
    # 直接候选
    cand = r.cache_dir / "cross-encoder" / "ms-marco-MiniLM-L-6-v2"
    cand.mkdir(parents=True)
    assert r._resolve_local_model("cross-encoder/ms-marco-MiniLM-L-6-v2") == cand.resolve()
    # modelscope 别名 rglob
    alias = r.cache_dir / "modelscope" / "ms-marco-MiniLM-L6-v2"
    alias.mkdir(parents=True)
    assert r._resolve_local_model("cross-encoder/ms-marco-MiniLM-L-6-v2") == cand.resolve()
    # 无 → None
    assert r._resolve_local_model("nope") is None


def test_reranker_load_local(monkeypatch, tmp_path):
    r = _rk(monkeypatch, tmp_path)
    p = tmp_path / "m"
    p.mkdir()
    monkeypatch.setattr(r, "_resolve_local_model", lambda n: p)
    m = r._load_model("m")
    assert isinstance(m, FakeCE)


def test_reranker_load_download(monkeypatch, tmp_path):
    r = _rk(monkeypatch, tmp_path)
    dl = tmp_path / "dl"
    dl.mkdir()
    monkeypatch.setattr(r, "_resolve_local_model", lambda n: None)
    monkeypatch.setattr("app.utils.model_download.download_model", lambda n, d: dl)
    assert isinstance(r._load_model("m"), FakeCE)


def test_reranker_load_download_fail(monkeypatch, tmp_path):
    r = _rk(monkeypatch, tmp_path)
    monkeypatch.setattr(r, "_resolve_local_model", lambda n: None)
    monkeypatch.setattr("app.utils.model_download.download_model",
                        lambda n, d: (_ for _ in ()).throw(RuntimeError("no net")))
    with pytest.raises(RuntimeError, match="Failed to download reranker"):
        r._load_model("m")


def test_rerank_empty_and_predict_fail(monkeypatch, tmp_path):
    r = _rk(monkeypatch, tmp_path)
    assert r.rerank("q", []) == []

    class BoomCE:
        def predict(self, pairs):
            raise RuntimeError("model down")
    r.model = BoomCE()
    docs = [{"content": "a"}, {"content": "b"}]
    out = r.rerank("q", docs, top_k=2)
    assert [(d["content"], s) for d, s in out] == [("a", 0.0), ("b", 0.0)]


def test_rerank_sorted_topk(monkeypatch, tmp_path):
    r = _rk(monkeypatch, tmp_path)
    r.model = FakeCE()
    docs = [{"content": "a"}, {"content": "b"}, {"content": "c"}]
    out = r.rerank("q", docs, top_k=2)
    assert [(d["content"], s) for d, s in out] == [("c", 2.0), ("a", 1.0)]