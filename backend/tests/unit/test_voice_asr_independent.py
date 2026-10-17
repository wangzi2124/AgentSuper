"""L1 单元测试：语音转写（ASR）与语音合成（TTS）解耦（agent.md §10.2）。

回归背景：`/api/voice/transcribe` 曾以 `svc.enabled`（= VOICE_TTS_ENABLED + TTS 模型）判门，
导致「只想语音输入、不装 TTS」的用户每次点击麦克风都拿到 503 → 前端实时链路静默吞错 →
表现为「前台说了话、后台没有任何反应」。转写应只因 Whisper 引擎/模型未就绪而 503。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import voice as voice_api


class FakeVoice:
    """最小 VoiceService 替身：显式区分 ASR 就绪与 TTS 启用。"""

    def __init__(self, *, whisper_ready: bool, tts_enabled: bool, text: str = "你好世界"):
        self._whisper_ready = whisper_ready
        self._tts_enabled = tts_enabled
        self._text = text

    @property
    def whisper_ready(self) -> bool:
        return self._whisper_ready

    @property
    def enabled(self) -> bool:
        return self._tts_enabled

    @property
    def has_model(self) -> bool:
        return self._tts_enabled

    def transcribe(self, audio_path: str):
        return True, self._text

    def synthesize(self, text, speaker="", language="Auto", instruct=""):
        return True, "out.wav", None


@pytest.fixture
def make_client():
    def _make(svc: FakeVoice) -> TestClient:
        app = FastAPI()
        app.include_router(voice_api.router, prefix="/api/voice")
        app.state.voice_service = svc
        return TestClient(app)

    return _make


def _wav_bytes() -> bytes:
    import io
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(16000)))
    return buf.getvalue()


def test_transcribe_works_without_tts(make_client):
    """ASR 就绪 + TTS 未启用 → 转写仍 200（不再被 TTS 总开关连坐）。"""
    c = make_client(FakeVoice(whisper_ready=True, tts_enabled=False))
    r = c.post("/api/voice/transcribe", files={"audio": ("a.wav", _wav_bytes(), "audio/wav")})
    assert r.status_code == 200
    assert r.json()["data"]["text"] == "你好世界"


def test_transcribe_503_when_asr_not_ready(make_client):
    c = make_client(FakeVoice(whisper_ready=False, tts_enabled=True))
    r = c.post("/api/voice/transcribe", files={"audio": ("a.wav", _wav_bytes(), "audio/wav")})
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == 503
    assert "转写引擎未就绪" in body["message"]


def test_status_exposes_asr_and_tts_flags(make_client):
    c = make_client(FakeVoice(whisper_ready=True, tts_enabled=False))
    data = c.get("/api/voice/status").json()["data"]
    assert data["asr_enabled"] is True
    assert data["enabled"] is False


def test_tts_still_gated_by_tts_switch(make_client):
    c = make_client(FakeVoice(whisper_ready=True, tts_enabled=False))
    r = c.post("/api/voice/tts", data={"text": "你好"})
    assert r.status_code == 503
    assert "语音合成未启用" in r.json()["message"]
