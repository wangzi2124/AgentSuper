# -*- coding: utf-8 -*-
"""voice API 路由用例：status/transcribe/tts（mock service，无真实模型）。

运行：pytest tests/test_api_voice.py
"""
import asyncio
import json
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

import pytest
from fastapi import UploadFile
from types import SimpleNamespace

import app.api.voice as voice_api


class FakeService:
    """替换 _service 的假实现：构造后可覆盖 transcribe/synthesize 行为。"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.has_model = True
        self.transcribe_result = (True, "识别文本")
        self.synth_result = None  # None → 自动写一个假 wav

    def transcribe(self, audio_path):
        return self.transcribe_result

    def synthesize(self, text, speaker="", language="Auto", instruct="", model_size=""):
        if self.synth_result is not None:
            return self.synth_result
        out = Path(tempfile.mkdtemp()) / "out.wav"
        out.write_bytes(b"RIFFfake")
        return True, str(out), out


def _req():
    """假 Request：app.state.voice_service 未注满 → _get_service 回退模块 _service（被 mock）。"""
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(voice_service=None))
    )


def _install(monkeypatch, enabled=True):
    fake = FakeService(enabled=enabled)
    monkeypatch.setattr(voice_api, "_service", fake)
    return fake


def _upload(data: bytes, name: str = "rec.webm"):
    return UploadFile(file=BytesIO(data), filename=name)


def _norm(resp):
    """路由可能返回 dict（FastAPI 自动序列化）或 Response，统一成 (status, body_bytes)。"""
    if isinstance(resp, dict):
        return 200, json.dumps(resp, ensure_ascii=False).encode("utf-8")
    return resp.status_code, resp.body


# ── status ─────────────────────────────────────────────────────────────────

def test_status_enabled(monkeypatch):
    _install(monkeypatch, enabled=True)
    resp = voice_api.voice_status(_req())
    assert resp["code"] == 0
    assert resp["data"]["enabled"] is True
    assert "Vivian" in resp["data"]["speakers"]


def test_status_disabled(monkeypatch):
    _install(monkeypatch, enabled=False)
    resp = voice_api.voice_status(_req())
    assert resp["code"] == 0
    assert resp["data"]["enabled"] is False


# ── transcribe ─────────────────────────────────────────────────────────────

def test_transcribe_success(monkeypatch):
    _install(monkeypatch, enabled=True)
    resp = asyncio.run(voice_api.voice_transcribe(_req(), audio=_upload(b"RIFFaudio")))
    status, body = _norm(resp)
    assert status == 200
    data = json.loads(body)
    assert data["code"] == 0
    assert data["data"]["text"] == "识别文本"


def test_transcribe_disabled(monkeypatch):
    _install(monkeypatch, enabled=False)
    resp = asyncio.run(voice_api.voice_transcribe(_req(), audio=_upload(b"RIFFaudio")))
    status, body = _norm(resp)
    assert status == 503
    assert json.loads(body)["code"] != 0


def test_transcribe_empty_audio(monkeypatch):
    _install(monkeypatch, enabled=True)
    resp = asyncio.run(voice_api.voice_transcribe(_req(), audio=_upload(b"")))
    status, body = _norm(resp)
    assert status == 400


def test_transcribe_failure(monkeypatch):
    fake = _install(monkeypatch, enabled=True)
    fake.transcribe_result = (False, "whisper down")
    resp = asyncio.run(voice_api.voice_transcribe(_req(), audio=_upload(b"RIFFaudio")))
    status, body = _norm(resp)
    assert status == 503
    data = json.loads(body)
    assert data["code"] != 0
    assert "whisper down" in data["message"]


# ── tts ────────────────────────────────────────────────────────────────────

def test_tts_success(monkeypatch):
    _install(monkeypatch, enabled=True)
    resp = asyncio.run(voice_api.voice_tts(_req(), text="你好"))
    assert resp.status_code == 200
    assert resp.media_type == "audio/wav"
    assert Path(resp.path).exists()


def test_tts_disabled(monkeypatch):
    _install(monkeypatch, enabled=False)
    resp = asyncio.run(voice_api.voice_tts(_req(), text="你好"))
    status, body = _norm(resp)
    assert status == 503
    assert json.loads(body)["code"] != 0


def test_tts_missing_text(monkeypatch):
    _install(monkeypatch, enabled=True)
    resp = asyncio.run(voice_api.voice_tts(_req(), text="   "))
    status, body = _norm(resp)
    assert status == 400


def test_tts_synthesis_failure(monkeypatch):
    fake = _install(monkeypatch, enabled=True)
    fake.synth_result = (False, "no model", None)
    resp = asyncio.run(voice_api.voice_tts(_req(), text="你好"))
    status, body = _norm(resp)
    assert status == 503
    data = json.loads(body)
    assert data["code"] != 0
    assert "no model" in data["message"]
