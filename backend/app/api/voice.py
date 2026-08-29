"""语音 API 路由：后端 subprocess 驱动 ttsclone（无外部 HTTP 接口调用）。

前端录音/朗读只访问本后端：
  - GET  /api/voice/status        可用性（enabled/speakers/languages）
  - POST /api/voice/transcribe    multipart audio → {code:0, data:{text}}
  - POST /api/voice/tts           form text/... → audio/wav 文件

统一信封（app/api/responses.py）与 AuthMiddleware/CORS 保护；未启用时返回 503 明确错误（不崩会话）。
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile

from app.api.responses import ok, error_response
from app.services.voice import LANGUAGES, SPEAKERS, create_voice_service

logger = logging.getLogger(__name__)

router = APIRouter()

_service = None


def _get_service():
    global _service
    if _service is None:
        _service = create_voice_service()
    return _service


@router.get("/status")
def voice_status():
    svc = _get_service()
    return ok({
        "enabled": svc.enabled,
        "speakers": SPEAKERS,
        "languages": LANGUAGES,
    })


@router.post("/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    svc = _get_service()
    if not svc.enabled:
        return error_response(503, "语音服务未启用（VOICE_TTS_ENABLED=false 或 ttsclone 缺失）", 503)
    try:
        data = await audio.read()
    except Exception as e:  # noqa: BLE001
        return error_response(400, f"读取音频失败: {e}", 400)
    if not data:
        return error_response(400, "空音频文件", 400)
    suffix = Path(audio.filename or "record.webm").suffix or ".webm"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        succ, text = await asyncio.to_thread(svc.transcribe, tmp_path)
        if not succ:
            return error_response(503, f"转写失败: {text}", 503)
        return ok({"text": text})
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@router.post("/tts")
async def voice_tts(
    text: str = Form(...),
    speaker: str = Form(""),
    language: str = Form("Auto"),
    instruct: str = Form(""),
):
    svc = _get_service()
    if not svc.enabled:
        return error_response(503, "语音服务未启用（VOICE_TTS_ENABLED=false 或 ttsclone 缺失）", 503)
    if not text or not text.strip():
        return error_response(400, "text is required", 400)
    succ, msg, path = await asyncio.to_thread(
        svc.synthesize, text.strip(), speaker, language, instruct
    )
    if not succ:
        return error_response(503, f"合成失败: {msg}", 503)
    from fastapi.responses import FileResponse
    return FileResponse(str(path), media_type="audio/wav", filename=Path(path).name)
