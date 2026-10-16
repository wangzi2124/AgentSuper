# -*- coding: utf-8 -*-
"""model_download.py 剩余分支用例（补 test_model_download.py）：_parse_content_range、
_resume_offset、_run_attempt 异常传播、_retry 成功/全失败、_resumable_download_once
206 续传起点不一致重建/200 全量/非 2xx、download_model 源回退与双失败。

运行：pytest tests/test_model_download_extra.py
"""
import os
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

from app.utils import model_download as md


# ── 小工具 ─────────────────────────────────────────────────────────────────

def test_parse_content_range():
    assert md._parse_content_range(None) is None
    assert md._parse_content_range("garbage") is None
    r = md._parse_content_range("bytes 100-199/1000")
    assert r == (100, 199, 1000)
    r2 = md._parse_content_range("bytes 0-99/*")
    assert r2 == (0, 99, None)


def test_resume_offset(tmp_path):
    assert md._resume_offset(tmp_path / "nope.part") == 0
    p = tmp_path / "x.part"
    p.write_bytes(b"12345")
    assert md._resume_offset(p) == 5


def test_run_attempt_propagates_worker_error():
    def boom():
        raise ValueError("worker crash")
    with pytest.raises(ValueError, match="worker crash"):
        md._run_attempt_with_timeout(boom, 5)


def test_run_attempt_timeout_zero_direct():
    calls = []
    assert md._run_attempt_with_timeout(lambda: calls.append(1) or "direct", 0) == "direct"
    assert calls == [1]


# ── _retry ─────────────────────────────────────────────────────────────────

def test_retry_success(monkeypatch):
    monkeypatch.setattr(md, "_DOWNLOAD_RETRIES", 2)
    out = md._retry("Test", lambda: "ok")
    assert out == "ok"


def test_retry_all_fail(monkeypatch):
    monkeypatch.setattr(md, "_DOWNLOAD_RETRIES", 1)

    def boom():
        raise RuntimeError("net down")
    with pytest.raises(RuntimeError, match="Failed to download model"):
        md._retry("Test", boom)


# ── _resumable_download_once ───────────────────────────────────────────────

class FakeResp:
    def __init__(self, status=200, headers=None, content=b"DATA", raise_err=False):
        self.status_code = status
        self.headers = headers or {}
        self._content = content
        self._raise_err = raise_err

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, size):
        yield self._content

    def raise_for_status(self):
        if self._raise_err or self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


def _mk(monkeypatch, tmp_path, resp):
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: resp)
    dest = tmp_path / "m.bin"
    part = dest.with_name(dest.name + ".part")
    return dest, part


def test_once_200_full_rebuild(monkeypatch, tmp_path):
    dest, part = _mk(monkeypatch, tmp_path, FakeResp(200, {}, b"FULL"))
    part.write_bytes(b"partial")
    md._resumable_download_once("u", dest, part, {}, 10, None)
    assert dest.read_bytes() == b"FULL"
    assert not part.exists()


def test_once_206_matching_offset_appends(monkeypatch, tmp_path):
    dest, part = _mk(monkeypatch, tmp_path, FakeResp(
        206, {"Content-Range": "bytes 3-5/8"}, b"TAIL"))
    part.write_bytes(b"012")
    md._resumable_download_once("u", dest, part, {}, 10, None)
    assert dest.read_bytes() == b"012TAIL"


def test_once_206_mismatch_offset_rebuilds(monkeypatch, tmp_path):
    # 服务器续传起点与我们不一致（0）→ wb 全量重建
    dest, part = _mk(monkeypatch, tmp_path, FakeResp(
        206, {"Content-Range": "bytes 0-3/8"}, b"FULL"))
    part.write_bytes(b"stale")
    md._resumable_download_once("u", dest, part, {}, 10, None)
    assert dest.read_bytes() == b"FULL"


def test_once_error_status_raises(monkeypatch, tmp_path):
    dest, part = _mk(monkeypatch, tmp_path, FakeResp(404, {}, raise_err=True))
    with pytest.raises(Exception):
        md._resumable_download_once("u", dest, part, {}, 10, None)


def test_once_unexpected_status_raises(monkeypatch, tmp_path):
    dest, part = _mk(monkeypatch, tmp_path, FakeResp(302, {}))
    with pytest.raises(RuntimeError, match="unexpected status"):
        md._resumable_download_once("u", dest, part, {}, 10, None)


# ── download_model 源回退 ──────────────────────────────────────────────────

def _mk_complete(root, name):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "model.bin").write_bytes(b"\x00" * 2_000_000)
    return d


def _fake_modelscope(module_name="modelscope", snap=None):
    import types
    m = types.ModuleType(module_name)
    if snap is not None:
        m.snapshot_download = snap
    sys.modules[module_name] = m


def test_download_model_modelscope_fallback_hf(monkeypatch, tmp_path):
    monkeypatch.setattr(md.settings, "model_download_timeout", 1)
    _mk_complete(tmp_path, "m")  # structured_path = cache/m

    def fake_ms_download(model_id, cache_dir=None, **kw):
        raise RuntimeError("modelscope down")
    _fake_modelscope(snap=fake_ms_download)

    def retry(label, fn, *a, **k):
        if label == "HuggingFace":
            return str(tmp_path / "m")
        raise RuntimeError("ms failed")
    monkeypatch.setattr(md, "_retry", retry)
    out = md.download_model("m", cache_dir=tmp_path)
    # HF 回退始终返回 structured_path（local_dir），与 _retry 返回值无关
    assert out == (tmp_path / "m").resolve()


def test_download_model_no_modelscope_uses_hf(monkeypatch, tmp_path):
    monkeypatch.setattr(md.settings, "model_download_timeout", 1)
    _fake_modelscope(snap=None)  # 无 snapshot_download → import 抛 ImportError
    full = _mk_complete(tmp_path, "m")
    monkeypatch.setattr(md, "_retry", lambda label, fn, *a, **k: str(full))
    out = md.download_model("m", cache_dir=tmp_path)
    assert out == full


def test_download_model_both_fail_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(md.settings, "model_download_timeout", 1)

    def fake_ms_download(model_id, cache_dir=None, **kw):
        raise RuntimeError("ms down")
    _fake_modelscope(snap=fake_ms_download)
    monkeypatch.setattr(md, "_retry", lambda label, fn, *a, **k: (_ for _ in ()).throw(RuntimeError("all down")))
    with pytest.raises(RuntimeError, match="Failed to download embedding model"):
        md.download_model("m", cache_dir=tmp_path)