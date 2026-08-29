# -*- coding: utf-8 -*-
"""voice service 全量用例：子进程解析、错误、超时、门控、合成/转写路径。

运行：pytest tests/test_voice_service.py
"""
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest

import app.services.voice as vs
from app.config import settings


def _make_service(monkeypatch, tmp_path, script=True, enabled=True):
    monkeypatch.setattr(settings, "voice_tts_enabled", enabled)
    tts_dir = tmp_path / "ttsclone"
    tts_dir.mkdir(exist_ok=True)
    if script:
        (tts_dir / "clone.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    return vs.VoiceService(
        tts_dir=str(tts_dir),
        speaker="Vivian",
        model_size="1.7B",
        timeout=10,
        output_dir=str(tmp_path / "out"),
    )


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── 门控 ───────────────────────────────────────────────────────────────────

def test_enabled_gate(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path, script=True, enabled=False)
    assert svc.enabled is False
    svc2 = _make_service(monkeypatch, tmp_path, script=True, enabled=True)
    assert svc2.enabled is True


def test_enabled_missing_script(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path, script=False, enabled=True)
    assert svc.enabled is False


def test_speaker_model_fallback():
    svc = vs.VoiceService(speaker="Nobody", model_size="99B", timeout=5)
    assert svc.speaker == "Vivian"
    assert svc.model_size == "1.7B"


# ── 子进程解析 ─────────────────────────────────────────────────────────────

def test_run_parses_json(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    proc = _Proc(returncode=0, stdout='some log\n{"ok": true, "text": "你好"}\n')
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
    ok, res = svc._run(["transcribe", "x.wav"])
    assert ok is True
    assert res["text"] == "你好"


def test_run_returns_raw_output(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    proc = _Proc(returncode=0, stdout="plain output line\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
    ok, res = svc._run(["custom", "hi"])
    assert ok is True
    assert "plain output" in res["output"]


def test_run_error_returncode(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    proc = _Proc(returncode=2, stdout="", stderr="boom")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: proc)
    ok, res = svc._run(["custom", "hi"])
    assert ok is False
    assert "boom" in res["error"]


def test_run_missing_script(tmp_path):
    svc = vs.VoiceService(tts_dir=str(tmp_path / "nope"), timeout=10)
    ok, res = svc._run(["custom", "hi"])
    assert ok is False
    assert "not found" in res["error"]


def test_run_timeout(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["x"], timeout=10)

    monkeypatch.setattr(subprocess, "run", _boom)
    ok, res = svc._run(["custom", "hi"])
    assert ok is False
    assert "timed out" in res["error"]


def test_run_missing_python(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise FileNotFoundError("python")

    monkeypatch.setattr(subprocess, "run", _boom)
    ok, res = svc._run(["custom", "hi"])
    assert ok is False
    assert "python not found" in res["error"]


# ── 合成 ───────────────────────────────────────────────────────────────────

def test_synthesize_writes_wav(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)

    def fake_run(args, timeout=None):
        idx = args.index("--output")
        Path(args[idx + 1]).write_bytes(b"RIFFfake")
        return True, {"ok": True, "path": args[idx + 1]}

    monkeypatch.setattr(svc, "_run", fake_run)
    ok, msg, path = svc.synthesize("你好")
    assert ok is True
    assert path is not None
    assert Path(path).exists()
    assert "tts_Vivian_" in Path(path).name


def test_synthesize_passes_args(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    captured = {}

    def fake_run(args, timeout=None):
        captured["args"] = args
        return False, {"error": "no model"}

    monkeypatch.setattr(svc, "_run", fake_run)
    svc.synthesize("测试", speaker="Dylan", language="Chinese", instruct="happy", model_size="0.6B")
    assert "--speaker" in captured["args"]
    assert "Dylan" in captured["args"]
    assert "--lang" in captured["args"]
    assert "--instruct" in captured["args"]
    assert "--small" in captured["args"]


def test_synthesize_failure(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    monkeypatch.setattr(svc, "_run", lambda args, timeout=None: (False, {"error": "no model"}))
    ok, msg, path = svc.synthesize("你好")
    assert ok is False
    assert "no model" in msg
    assert path is None


# ── 转写 ───────────────────────────────────────────────────────────────────

def test_transcribe_text(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(svc, "_run", lambda args, timeout=None: (True, {"text": "今天天气不错"}))
    ok, text = svc.transcribe(str(audio))
    assert ok is True
    assert text == "今天天气不错"


def test_transcribe_missing_audio(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    ok, text = svc.transcribe(str(tmp_path / "nope.wav"))
    assert ok is False
    assert "not found" in text


def test_transcribe_failure(monkeypatch, tmp_path):
    svc = _make_service(monkeypatch, tmp_path)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(svc, "_run", lambda args, timeout=None: (False, {"error": "whisper failed"}))
    ok, text = svc.transcribe(str(audio))
    assert ok is False
    assert "whisper failed" in text
