# -*- coding: utf-8 -*-
"""C3 模型下载断点续传 / 完整性自愈测试（本地 HTTP Range 服务器，不依赖外网）。

覆盖：
  - resumable_download：续传（Range 起点=已下载字节）、无 Range 服务器全量重建、
    首次断流后重试续传、.part 原子替换、expected_size 校验
  - 快照缓存完整性：_looks_complete 启发式 / _accept 哨兵优先 + 补哨兵
  - download_model：完整缓存免网络复用；局部缓存自动续传补齐
  - 串行化重试：僵尸线程未回收 → _DownloadAbandoned → 终止本轮（不再并发写缓存）
运行：pytest tests/test_model_download.py
"""
import os
import re
import socket
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.utils import model_download as md
from app.config import settings


# ---------------------------------------------------------------------------
# 本地 HTTP 测试服务器（支持/忽略 Range，可断流，可只响应一次）
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    payload = b""
    support_range = True
    fail_first = 0          # 前 N 次请求故意断流
    requests = []

    def log_message(self, *a):  # silence
        pass

    def _first_requests(self):
        _Handler.requests.append(dict(
            range=self.headers.get("Range"),
            path=self.path,
        ))
        return len(_Handler.requests) - 1

    def do_GET(self):
        idx = self._first_requests()
        if idx < self.fail_first:
            # 断流：只写一半然后掐断连接
            half = self.payload[: max(1, len(self.payload) // 2)]
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            try:
                self.wfile.write(half)
                self.wfile.flush()
            except OSError:
                pass
            self.connection.shutdown(socket.SHUT_RDWR)
            return

        raw = self.headers.get("Range")
        if self.support_range and raw:
            m = re.match(r"bytes=(\d+)-$", raw)
            if m:
                start = int(m.group(1))
                data = self.payload[start:]
                self.send_response(206)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)


def _serve(handler_cls=_Handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


@pytest.fixture
def http_srv(tmp_path):
    _Handler.payload = os.urandom(128_000)
    _Handler.support_range = True
    _Handler.fail_first = 0
    _Handler.requests = []
    srv = _serve()
    yield lambda path: f"http://127.0.0.1:{srv.server_port}/{path}"
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------------------
# resumable_download
# ---------------------------------------------------------------------------

def test_resume_from_partial(tmp_path, http_srv):
    payload = _Handler.payload
    dest = tmp_path / "model.bin"
    part = dest.with_name(dest.name + ".part")
    first = payload[:20_000]
    part.write_bytes(first)

    md.resumable_download(http_srv("m"), dest, timeout=30, retries=0, expected_size=len(payload))

    assert dest.read_bytes() == payload
    assert not part.exists()
    assert _Handler.requests[-1]["range"] == f"bytes={len(first)}-"


def test_fresh_download_no_range_but_complete(tmp_path, http_srv):
    payload = _Handler.payload
    dest = tmp_path / "m.bin"
    md.resumable_download(http_srv("m"), dest, timeout=30, retries=0, expected_size=len(payload))
    assert dest.read_bytes() == payload
    assert len(_Handler.requests) == 1


def test_server_ignores_range_restarts(tmp_path, http_srv):
    payload = _Handler.payload
    dest = tmp_path / "m.bin"
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(b"garbage-half")
    _Handler.support_range = False
    md.resumable_download(http_srv("m"), dest, timeout=30, retries=0, expected_size=len(payload))
    assert dest.read_bytes() == payload
    assert _Handler.requests[0]["range"] is not None  # 请求带了 Range，但服务器忽略即可


def test_retry_resumes_after_connection_drop(tmp_path, http_srv, monkeypatch):
    payload = _Handler.payload
    monkeypatch.setattr(md, "_CHUNK_SIZE", 1024)  # 分块变小，断流前已落盘部分字节
    _Handler.fail_first = 1
    dest = tmp_path / "m.bin"
    md.resumable_download(http_srv("m"), dest, timeout=30, retries=2, expected_size=len(payload))
    assert dest.read_bytes() == payload
    assert len(_Handler.requests) == 2
    assert _Handler.requests[1]["range"] is not None  # 第二次续传


def test_size_mismatch_fails_and_keeps_part(tmp_path, http_srv):
    dest = tmp_path / "m.bin"
    with pytest.raises(RuntimeError, match="size mismatch"):
        md.resumable_download(http_srv("m"), dest, timeout=30, retries=0,
                              expected_size=len(_Handler.payload) + 999)
    assert dest.with_name(dest.name + ".part").exists()  # partial 保留待续传


# ---------------------------------------------------------------------------
# resumable_download 超时/串行化
# ---------------------------------------------------------------------------

def test_run_attempt_timeout_raises_abandoned(monkeypatch):
    monkeypatch.setattr(md, "_ZOMBIE_GRACE", 0.01)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.5)
        return "late"

    t0 = time.monotonic()
    with pytest.raises(md._DownloadAbandoned):
        md._run_attempt_with_timeout(slow, 0.05)
    assert time.monotonic() - t0 < 0.3  # 回收软上限生效，未等慢函数跑完
    assert len(calls) == 1


def test_run_attempt_returns_value():
    assert md._run_attempt_with_timeout(lambda: 42, 5) == 42


def test_retry_terminates_on_zombie(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        return "x"

    def zombified(*a, **k):
        fn()  # 这轮尝试确实发起过
        raise md._DownloadAbandoned("still running")

    monkeypatch.setattr(md, "_run_attempt_with_timeout", zombified)
    with pytest.raises(RuntimeError, match="abandoned"):
        md._retry("Test", fn)
    assert len(calls) == 1  # 僵尸后立即终止，不再并发发起下一次尝试


# ---------------------------------------------------------------------------
# 快照缓存完整性 / download_model
# ---------------------------------------------------------------------------

def _mk_complete(root, name, big=2_000_000):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "model.bin").write_bytes(os.urandom(big))
    return d


def test_looks_complete(tmp_path):
    good = _mk_complete(tmp_path, "good")
    assert md._looks_complete(good) is True
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "config.json").write_text("{}", encoding="utf-8")
    assert md._looks_complete(partial) is False      # 只有配置没有权重
    empty = tmp_path / "empty"
    empty.mkdir()
    assert md._looks_complete(empty) is False
    assert md._looks_complete(tmp_path / "nope.txt") is False


def test_accept_marker_and_backfill(tmp_path):
    good = _mk_complete(tmp_path, "good")
    assert md._accept(good) is True
    assert (good / md._COMPLETE_MARKER).exists()     # 补齐哨兵
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "config.json").write_text("{}", encoding="utf-8")
    assert md._accept(partial) is False              # 不完整 → 交给续传
    (partial / md._COMPLETE_MARKER).touch()
    assert md._accept(partial) is True               # 哨兵优先


def test_download_model_uses_complete_cache(tmp_path):
    good = _mk_complete(tmp_path, "BAAI/bge-small-zh-v1.5")
    got = md.download_model("BAAI/bge-small-zh-v1.5", cache_dir=tmp_path)
    assert got == good
    assert (good / md._COMPLETE_MARKER).exists()


def test_download_model_resumes_partial_via_modelscope(tmp_path, monkeypatch):
    partial = tmp_path / "BAAI" / "bge-small-zh-v1.5"
    partial.mkdir(parents=True)
    (partial / "config.json").write_text("{}", encoding="utf-8")  # 残缺：仅有配置

    full = tmp_path / "BAAI" / "bge-small-zh-v1.5-full"
    _mk_complete(tmp_path, "BAAI/bge-small-zh-v1.5-full")

    class _FakeSnap:
        def snapshot_download(self, model_id, cache_dir=None, **kw):
            import shutil
            shutil.rmtree(partial)
            shutil.copytree(full, partial)
            return str(partial)

    fake = _FakeSnap()
    monkeypatch.setitem(sys.modules, "modelscope", fake)

    got = md.download_model("BAAI/bge-small-zh-v1.5", cache_dir=tmp_path)
    assert got == partial
    assert (partial / md._COMPLETE_MARKER).exists()
    assert isinstance(settings.model_download_timeout, int)