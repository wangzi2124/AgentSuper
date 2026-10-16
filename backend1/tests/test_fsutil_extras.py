# -*- coding: utf-8 -*-
"""fsutil.py 新增接口用例(规格 §4.1/§4.2 补齐)：

  - FileSystemError / FileStat / stat
  - read_file(字节) / write_file(原子写) / append_file / remove
  - write_json 原子替换(无 .tmp 残留)
  - file_lock 文件级可重入锁(互斥 + 超时)
"""
import threading
import time
from pathlib import Path

import pytest

from app.filesystem import fsutil


class TestFileSystemErrorAndStat:
    def test_error_is_runtime_error(self):
        assert issubclass(fsutil.FileSystemError, RuntimeError)

    def test_stat_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        st = fsutil.stat(str(f))
        assert st is not None
        assert st.type == "File"
        assert st.size == 5
        assert st.mtime_ns > 0

    def test_stat_dir_and_missing(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        assert fsutil.stat(str(d)).type == "Directory"
        assert fsutil.stat(str(tmp_path / "nope")) is None


class TestReadWriteAppendRemove:
    def test_read_file_bytes(self, tmp_path):
        f = tmp_path / "b.bin"
        f.write_bytes(b"\x00\x01\x02")
        assert fsutil.read_file(str(f)) == b"\x00\x01\x02"

    def test_write_file_creates_dirs_and_bytes(self, tmp_path):
        p = tmp_path / "deep" / "f.bin"
        fsutil.write_file(str(p), b"\x00\xff")
        assert p.read_bytes() == b"\x00\xff"

    def test_write_file_str_and_atomic_no_tmp(self, tmp_path):
        p = tmp_path / "out.txt"
        fsutil.write_file(str(p), "你好")
        assert p.read_text(encoding="utf-8") == "你好"
        leftovers = [x for x in tmp_path.iterdir() if ".tmp-" in x.name]
        assert leftovers == []

    def test_write_file_overwrites_atomically(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("old", encoding="utf-8")
        fsutil.write_file(str(p), "new")
        assert p.read_text(encoding="utf-8") == "new"
        assert not [x for x in tmp_path.iterdir() if ".tmp-" in x.name]

    def test_append_file_create_then_append(self, tmp_path):
        p = tmp_path / "log.txt"
        fsutil.append_file(str(p), "a\n")
        fsutil.append_file(str(p), "b\n")
        assert p.read_text(encoding="utf-8") == "a\nb\n"

    def test_append_file_bytes(self, tmp_path):
        p = tmp_path / "b.bin"
        fsutil.append_file(str(p), b"\x01")
        fsutil.append_file(str(p), b"\x02")
        assert p.read_bytes() == b"\x01\x02"

    def test_remove_file_missing_ok(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("x", encoding="utf-8")
        fsutil.remove(str(p))
        assert not p.exists()
        fsutil.remove(str(tmp_path / "nope"))  # 静默成功

    def test_remove_dir_recursive(self, tmp_path):
        d = tmp_path / "d"
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "f.txt").write_text("f", encoding="utf-8")
        fsutil.remove(str(d))
        assert not d.exists()

    def test_write_json_atomic_roundtrip(self, tmp_path):
        p = tmp_path / "cfg" / "c.json"
        fsutil.write_json(str(p), {"k": "v"})
        assert fsutil.read_json(str(p)) == {"k": "v"}
        assert not [x for x in (tmp_path / "cfg").iterdir() if ".tmp-" in x.name]


class TestFileLock:
    def test_reentrant_same_thread(self, tmp_path):
        p = tmp_path / "locked.txt"
        order = []
        with fsutil.file_lock(str(p)):
            order.append("outer")
            with fsutil.file_lock(str(p)):
                order.append("inner")
        assert order == ["outer", "inner"]

    def test_twice_serializes_different_threads(self, tmp_path):
        p = tmp_path / "t.txt"
        seq = []

        def worker(n):
            with fsutil.file_lock(str(p)):
                seq.append(f"start{n}")
                time.sleep(0.1)
                seq.append(f"end{n}")

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start(); t2.start(); t1.join(); t2.join()
        # 同一路径锁保证 start1..end1, start2..end2 不交错
        assert seq.index("start1") < seq.index("end1") < seq.index("start2") < seq.index("end2") or \
               seq.index("start2") < seq.index("end2") < seq.index("start1") < seq.index("end1")

    def test_timeout_raises(self, tmp_path):
        p = tmp_path / "tt.txt"
        acquired = []
        release = threading.Event()
        done = threading.Event()

        def hold():
            with fsutil.file_lock(str(p)):
                acquired.append("held")
                release.set()
                time.sleep(0.3)

        t = threading.Thread(target=hold)
        t.start()
        assert release.wait(timeout=2)
        with pytest.raises(fsutil.FileSystemError):
            with fsutil.file_lock(str(p), timeout=0.05):
                acquired.append("main")
        t.join()
        assert acquired == ["held"]

    def test_different_paths_not_serialized(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        both = []
        with fsutil.file_lock(str(a)), fsutil.file_lock(str(b)):
            both.append("a+b")
        assert both == ["a+b"]