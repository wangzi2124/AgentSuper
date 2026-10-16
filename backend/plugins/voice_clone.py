"""
Voice Clone Plugin (subprocess architecture)

Delegates all voice operations to voiceclone-main via subprocess.
Zero voice dependencies in the AgentSuper backend — complete environment isolation.
"""
import json
import os
import subprocess
import datetime
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_NAME = "voice-clone"
PLUGIN_VERSION = "0.2.0"
PLUGIN_DESCRIPTION = "Voice cloning and TTS via isolated Qwen3-TTS subprocess"

_BASE_DIR = Path(__file__).resolve().parents[1]
_OUTPUT_DIR = _BASE_DIR / "data" / "generated"
_VOICECLONE_DIR = _BASE_DIR / "ttsclone"


def _get_python():
    venv_python = _VOICECLONE_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return "python"


def _run_voice(args: list[str], timeout: int = 600) -> dict:
    cmd = [_get_python(), str(_VOICECLONE_DIR / "clone.py")] + args
    logger.info("Running voice command: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_VOICECLONE_DIR),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            return {"ok": False, "error": stderr or stdout or "process exited with error"}
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return {"ok": True, "output": stdout}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "error": f"Python not found: {_get_python()}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


AVAILABLE_SPEAKERS = [
    "Vivian", "Serena", "Uncle_fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_anna", "Sohee",
]

AVAILABLE_LANGUAGES = [
    "Auto", "Chinese", "English", "Japanese", "Korean",
    "French", "German", "Spanish", "Portuguese", "Russian", "Italian",
]


def tool_voice_clone(
    text: str,
    ref_audio: str,
    ref_text: str = "",
    language: str = "Auto",
    model_size: str = "1.7B",
) -> str:
    """
    Clone a voice from a reference audio file and synthesize new speech.

    Parameters:
    - text: The text to synthesize with the cloned voice
    - ref_audio: Path to reference audio file (5-15 seconds recommended)
    - ref_text: Transcript of the reference audio (optional, improves quality)
    - language: Auto, Chinese, English, Japanese, Korean, French, German, Spanish, Portuguese, Russian, Italian
    - model_size: "0.6B" (faster) or "1.7B" (better quality)
    """
    if not text or not text.strip():
        return "Error: text parameter is required"
    if not ref_audio or not os.path.isfile(ref_audio):
        return f"Error: reference audio file not found: {ref_audio}"
    if model_size not in ("0.6B", "1.7B"):
        return "Error: model_size must be '0.6B' or '1.7B'"
    if not _VOICECLONE_DIR.exists():
        return f"Error: voiceclone-main not found at {_VOICECLONE_DIR}"

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = str(_OUTPUT_DIR / f"clone_{ts}.wav")

    args = ["clone", text.strip(), "--ref-audio", ref_audio, "--lang", language, "--output", outfile]
    if ref_text and ref_text.strip():
        args += ["--ref-text", ref_text.strip()]
    if model_size == "0.6B":
        args.append("--small")

    res = _run_voice(args, timeout=600)
    if res.get("ok"):
        path = res.get("path", outfile)
        return f"Voice clone success! Output: {path}"
    return f"Error: {res.get('error', 'unknown error')}"


def tool_custom_voice(
    text: str,
    speaker: str = "Vivian",
    instruct: str = "",
    language: str = "Auto",
    model_size: str = "1.7B",
) -> str:
    """
    Synthesize speech using a preset character voice with optional emotion/style control.

    Parameters:
    - text: The text to synthesize
    - speaker: Vivian, Serena, Uncle_fu, Dylan, Eric, Ryan, Aiden, Ono_anna, Sohee
    - instruct: Optional style/emotion instruction (e.g. "speak happily")
    - language: Auto, Chinese, English, Japanese, Korean, French, German, Spanish, Portuguese, Russian, Italian
    - model_size: "0.6B" or "1.7B"
    """
    if not text or not text.strip():
        return "Error: text parameter is required"
    if model_size not in ("0.6B", "1.7B"):
        return "Error: model_size must be '0.6B' or '1.7B'"
    if not _VOICECLONE_DIR.exists():
        return f"Error: voiceclone-main not found at {_VOICECLONE_DIR}"

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = str(_OUTPUT_DIR / f"tts_{speaker}_{ts}.wav")

    args = ["custom", text.strip(), "--speaker", speaker, "--lang", language, "--output", outfile]
    if instruct and instruct.strip():
        args += ["--instruct", instruct.strip()]
    if model_size == "0.6B":
        args.append("--small")

    res = _run_voice(args, timeout=600)
    if res.get("ok"):
        path = res.get("path", outfile)
        return f"Custom voice success! Output: {path} (speaker={speaker})"
    return f"Error: {res.get('error', 'unknown error')}"


def tool_voice_design(
    text: str,
    voice_description: str,
    language: str = "Auto",
) -> str:
    """
    Design a completely new voice using natural language description (1.7B model only).

    Parameters:
    - text: The text to synthesize
    - voice_description: Natural language description of the desired voice characteristics
    - language: Auto, Chinese, English, Japanese, Korean, French, German, Spanish, Portuguese, Russian, Italian
    """
    if not text or not text.strip():
        return "Error: text parameter is required"
    if not voice_description or not voice_description.strip():
        return "Error: voice_description parameter is required"
    if not _VOICECLONE_DIR.exists():
        return f"Error: voiceclone-main not found at {_VOICECLONE_DIR}"

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = str(_OUTPUT_DIR / f"design_{ts}.wav")

    args = ["design", text.strip(), "--instruct", voice_description.strip(), "--lang", language, "--output", outfile]

    res = _run_voice(args, timeout=600)
    if res.get("ok"):
        path = res.get("path", outfile)
        return f"Voice design success! Output: {path}"
    return f"Error: {res.get('error', 'unknown error')}"


def tool_voice_transcribe(audio_path: str) -> str:
    """
    Transcribe audio to text using Whisper ASR.

    Parameters:
    - audio_path: Path to audio file (WAV, MP3, FLAC, etc.)
    """
    if not audio_path or not os.path.isfile(audio_path):
        return f"Error: audio file not found: {audio_path}"
    if not _VOICECLONE_DIR.exists():
        return f"Error: voiceclone-main not found at {_VOICECLONE_DIR}"

    args = ["transcribe", audio_path]
    res = _run_voice(args, timeout=300)
    if res.get("ok"):
        text = res.get("text", res.get("output", ""))
        return f"Transcription: {text}"
    return f"Error: {res.get('error', 'unknown error')}"
