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
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.responses import ok, error_response
from app.services.voice import LANGUAGES, SPEAKERS, create_voice_service

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_VOICE_SECONDS = 60.0


def _voice_dir() -> Path:
    """语音消息音频根目录：backend/data/voice/（原始 webm/ogg 原样留存，不转码）。"""
    from app.storage.paths import global_paths
    d = global_paths()["data"] / "voice"
    d.mkdir(parents=True, exist_ok=True)
    return d

_service = None


def _get_service(request: Request):
    """优先使用运行时注入的共享实例（app.state.voice_service，与主 Agent 工具同源），
    避免重复创建导致多个常驻 Whisper 子进程；仅测试/独立场景回退本模块懒单例。"""
    shared = getattr(request.app.state, "voice_service", None)
    if shared is not None:
        return shared
    global _service
    if _service is None:
        _service = create_voice_service()
    return _service


_DISABLED_MSG = (
    "语音服务未启用或模型未下载（VOICE_TTS_ENABLED=false / ttsclone 缺失 / "
    "模型缺失——请先运行 scripts/download_tts_model.py 预下载）"
)


def _disabled() -> dict:
    return error_response(503, _DISABLED_MSG, 503)


ALLOWED_AUDIO_EXTS = {".webm", ".ogg", ".wav", ".mp3", ".m4a"}


@router.post("/message")
async def voice_message_upload(
    request: Request,
    audio: UploadFile = File(...),
    duration: str = Form("0"),
    waveform: str = Form(""),
    text: str = Form(""),
):
    """保存一条语音消息的原始音频，返回可播放标识（不依赖 ttsclone 合成模型）。

    原始 webm/ogg 原样落 data/voice/<id>.<ext>（播放无需转码）。
    """
    try:
        data = await audio.read()
    except Exception as e:  # noqa: BLE001
        return error_response(400, f"读取音频失败: {e}", 400)
    if not data:
        return error_response(400, "空音频文件", 400)

    try:
        dur = float(duration or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur <= 0 or dur > MAX_VOICE_SECONDS:
        return error_response(400, f"语音时长无效（0<时长≤{MAX_VOICE_SECONDS:.0f}s）", 400)

    suffix = (Path(audio.filename or "voice.webm").suffix or ".webm").lower()
    if suffix not in ALLOWED_AUDIO_EXTS:
        suffix = ".webm"
    audio_id = f"{uuid.uuid4().hex[:16]}"
    fname = f"{audio_id}{suffix}"
    dst = _voice_dir() / fname
    try:
        with open(dst, "wb") as fp:
            fp.write(data)
    except Exception as e:  # noqa: BLE001
        logger.warning("save voice message failed: %s", e)
        return error_response(500, "保存语音失败", 500)

    wf = waveform.strip() or "[]"
    try:
        wf_list = [float(x) for x in wf.split(",")] if wf not in ("", "[]") else []
        wf_list = [max(0.0, min(1.0, x)) for x in wf_list]
    except (TypeError, ValueError):
        wf_list = []
    if len(wf_list) > 200:
        wf_list = wf_list[:200]

    url = f"/api/voice/audio/{fname}"
    return ok({
        "id": audio_id,
        "url": url,
        "duration": round(dur, 1),
        "waveform": wf_list,
        "text": (text or "").strip(),
    })


@router.get("/audio/{filename:path}")
def voice_message_audio(filename: str):
    """播放一条语音消息（历史回放也走这里，按 id 定位文件）。"""
    name = Path(filename).name
    if not name or name not in os.listdir(_voice_dir()):
        return error_response(404, "语音文件不存在或已清理", 404)
    path = _voice_dir() / name
    ext = Path(name).suffix.lower()
    media = {
        ".webm": "audio/webm", ".ogg": "audio/ogg", ".wav": "audio/wav",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    }.get(ext, "application/octet-stream")
    return FileResponse(str(path), media_type=media, filename=name)


@router.get("/status")
def voice_status(request: Request):
    svc = _get_service(request)
    return ok({
        "enabled": svc.enabled,
        "speakers": SPEAKERS,
        "languages": LANGUAGES,
        "has_model": svc.has_model,
    })


@router.post("/transcribe")
async def voice_transcribe(request: Request, audio: UploadFile = File(...)):
    svc = _get_service(request)
    if not svc.enabled:
        return _disabled()
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
    request: Request,
    text: str = Form(...),
    speaker: str = Form(""),
    language: str = Form("Auto"),
    instruct: str = Form(""),
):
    svc = _get_service(request)
    if not svc.enabled:
        return _disabled()
    if not text or not text.strip():
        return error_response(400, "text is required", 400)
    succ, msg, path = await asyncio.to_thread(
        svc.synthesize, text.strip(), speaker, language, instruct
    )
    if not succ:
        return error_response(503, f"合成失败: {msg}", 503)
    from fastapi.responses import FileResponse
    return FileResponse(str(path), media_type="audio/wav", filename=Path(path).name)
