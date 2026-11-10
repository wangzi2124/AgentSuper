from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.local_models import importer


def test_file_digest(tmp_path: Path):
    target = tmp_path / "a.bin"
    target.write_bytes(b"hello")
    assert importer.file_digest(target) == (
        "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_legacy_modelfile_lists_all_shards(tmp_path: Path):
    a = tmp_path / "m-00001-of-00002.gguf"
    b = tmp_path / "m-00002-of-00002.gguf"
    text = importer._legacy_modelfile([a, b], 4096)
    assert text.count("FROM") == 2
    assert "PARAMETER num_ctx 4096" in text


def test_create_prefers_files_api(tmp_path: Path, monkeypatch):
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(importer, "upload_blob", lambda _p: "sha256:" + "a" * 64)
    calls: list[dict] = []

    def fake_request(url, data, headers, timeout):
        calls.append({"url": url, "body": json.loads(data.decode())})
        return 200, '{"status":"success"}'

    monkeypatch.setattr(importer, "_request", fake_request)
    importer.create_ollama_model("my-model", [gguf], 8192)
    assert len(calls) == 1
    body = calls[0]["body"]
    assert body["model"] == "my-model"
    assert body["files"] == {"m.gguf": "sha256:" + "a" * 64}
    assert body["parameters"] == {"num_ctx": 8192}


def test_create_falls_back_to_modelfile(tmp_path: Path, monkeypatch):
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(importer, "upload_blob", lambda _p: "sha256:" + "b" * 64)
    responses = iter([(400, '{"error":"neither \'from\' or \'files\' was specified"}'), (200, '{"status":"success"}')])
    calls: list[dict] = []

    def fake_request(url, data, headers, timeout):
        calls.append(json.loads(data.decode()))
        return next(responses)

    monkeypatch.setattr(importer, "_request", fake_request)
    importer.create_ollama_model("my-model", [gguf], 4096)
    assert len(calls) == 2
    assert "modelfile" in calls[1]
    assert "FROM" in calls[1]["modelfile"]
    assert "PARAMETER num_ctx 4096" in calls[1]["modelfile"]


def test_create_reports_both_failures(tmp_path: Path, monkeypatch):
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(importer, "upload_blob", lambda _p: "sha256:" + "c" * 64)
    monkeypatch.setattr(importer, "_request", lambda *a, **k: (400, '{"error":"boom"}'))
    with pytest.raises(importer.ImportError_) as exc:
        importer.create_ollama_model("my-model", [gguf])
    assert "boom" in str(exc.value)


def test_create_rejects_missing_file(tmp_path: Path):
    with pytest.raises(importer.ImportError_):
        importer.create_ollama_model("my-model", [tmp_path / "missing.gguf"])


def test_delete_ollama_model_ignores_missing(monkeypatch):
    monkeypatch.setattr(importer, "_request", lambda *a, **k: (404, '{"error":"model not found"}'))
    importer.delete_ollama_model("gone")


def test_delete_ollama_model_uses_delete_method(monkeypatch):
    calls: list[dict] = []

    def fake_request(url, data, headers, timeout, method="POST"):
        calls.append({"url": url, "method": method, "body": json.loads(data.decode())})
        return 200, ""

    monkeypatch.setattr(importer, "_request", fake_request)
    importer.delete_ollama_model("m")
    assert calls == [{"url": f"{importer.ollama_base_url()}/api/delete", "method": "DELETE", "body": {"model": "m"}}]


def test_delete_ollama_model_reports_failure(monkeypatch):
    monkeypatch.setattr(importer, "_request", lambda *a, **k: (500, '{"error":"boom"}'))
    with pytest.raises(importer.ImportError_):
        importer.delete_ollama_model("m")


def test_delete_ollama_model_noops_on_empty_name(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "_request", lambda *a, **k: calls.append(a) or (200, ""))
    importer.delete_ollama_model("")
    assert calls == []
