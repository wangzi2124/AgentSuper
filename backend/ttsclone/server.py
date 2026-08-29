# -*- coding: utf-8 -*-
"""
Qwen3-TTS 本地语音合成 REST API
供外部服务(如 OMNI STUDIO 后端)调用。

启动:
    uv run uvicorn server:app --host 0.0.0.0 --port 7861

接口:
    GET  /health              服务与 GPU 状态
    POST /api/tts/custom      CustomVoice TTS(预设角色):text/speaker/language/instruct/model_size
    POST /api/tts/design      Voice Design(声音设计):text/language/voice_description
    POST /api/tts/clone       Voice Clone(声音复制,multipart):ref_audio + ref_text + target_text + ...
    以上成功均返回 audio/wav 二进制。
"""

import io
import os

import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import app as tts

app = FastAPI(title="VoiceClone API")

os.makedirs(tts.OUTPUT_DIR, exist_ok=True)


def _audio_response(audio, status: str, prefix: str):
    """(sr, wav) tuple -> 已保存的 wav 文件响应;失败返回 JSON 错误"""
    if audio is None:
        return JSONResponse({"ok": False, "error": status}, status_code=500)
    sr, wav = audio
    try:
        path = tts._save_output([wav], sr, prefix)
        return FileResponse(path, media_type="audio/wav", filename=os.path.basename(path))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _read_upload_audio(upload: UploadFile):
    """读取上传音频为 (wav float32, sr) tuple"""
    try:
        data = upload.file.read()
        wav, sr = sf.read(io.BytesIO(data), dtype="float32")
        return wav, int(sr)
    except Exception:
        return None


def _ensure_model(model_type: str, model_size: str):
    """校验模型已预下载（不自动下载）。缺失时抛错，由调用方返回 500。"""
    model_id = f"Qwen/Qwen3-TTS-12Hz-{model_size}-{model_type}"
    local_path = os.path.join(tts.LOCAL_MODELS_DIR, model_id.replace("/", "_"))
    if not (os.path.exists(local_path) and os.path.isdir(local_path)):
        raise RuntimeError(
            f"模型未下载: {model_id}\n"
            f"请先运行 backend/scripts/download_tts_model.py 预下载（模型不会自动下载）。"
        )


@app.get("/health")
def health():
    return {
        "ok": True,
        "gpu": tts._gpu_status(),
        "cachedModels": len(tts._loaded_models),
        "speakers": tts.SPEAKERS,
        "languages": tts.LANGUAGES,
        "device": tts.DEVICE,
    }


@app.post("/api/tts/custom")
async def tts_custom(
    text: str = Form(...),
    speaker: str = Form("Vivian"),
    language: str = Form("Auto"),
    instruct: str = Form(""),
    model_size: str = Form("1.7B"),
):
    if model_size not in tts.MODEL_SIZES:
        model_size = "1.7B"
    _ensure_model("CustomVoice", model_size)
    audio, status = tts.generate_custom_voice(
        text, language, speaker, instruct.strip() or None, model_size
    )
    return _audio_response(audio, status, f"tts_{speaker}")


@app.post("/api/tts/design")
async def tts_design(
    text: str = Form(...),
    language: str = Form("Auto"),
    voice_description: str = Form(...),
):
    _ensure_model("VoiceDesign", "1.7B")
    audio, status = tts.generate_voice_design(text, language, voice_description)
    return _audio_response(audio, status, "design")


@app.post("/api/tts/clone")
async def tts_clone(
    target_text: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(""),
    language: str = Form("Auto"),
    use_xvector_only: bool = Form(False),
    model_size: str = Form("1.7B"),
):
    if model_size not in tts.MODEL_SIZES:
        model_size = "1.7B"
    audio_tuple = _read_upload_audio(ref_audio)
    if audio_tuple is None:
        return JSONResponse({"ok": False, "error": "无法解析参考音频"}, status_code=400)
    _ensure_model("Base", model_size)
    audio, status = tts.generate_voice_clone(
        audio_tuple, ref_text.strip(), target_text, language, use_xvector_only, model_size
    )
    return _audio_response(audio, status, "clone")


@app.post("/api/tts/transcribe")
async def tts_transcribe(audio: UploadFile = File(...)):
    """Whisper 自动转写:上传参考音频 → 返回识别文本(供 Voice Clone 自动填充 ref_text,免手动输入)"""
    wav, sr = _read_upload_audio(audio)  # _read_upload_audio 返回 (wav, sr)
    if wav is None:
        return JSONResponse({"ok": False, "error": "无法解析音频"}, status_code=400)
    try:
        text = tts.transcribe_audio((sr, wav))  # transcribe_audio 期望 (sr, wav)
        return {"ok": True, "text": text}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7861)
