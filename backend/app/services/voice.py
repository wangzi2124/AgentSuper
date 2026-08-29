"""语音服务：子进程驱动本地 Qwen3-TTS（ttsclone CLI），无外部 HTTP 接口调用。

与 `backend/plugins/voice_clone.py` 同源的 subprocess 架构（进程级隔离，
后端 venv 零语音依赖）；本 service 为**唯一实现**，供：
  - `/api/voice/*` 路由（前端录音转写 + 朗读）
  - 主 Agent 语音工具（`tool_tts_synthesize` / `tool_voice_transcribe`）
共用，避免双实现漂移。

阻塞的 subprocess.run 由调用方经 `asyncio.to_thread` 执行（见 api/voice.py 与
graphmod/tools.py），不阻塞 SelectorEventLoop（对齐 tool_execute 线程桥设计）。
"""
import datetime
import json
import logging
import subprocess
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

SPEAKERS = [
    "Vivian", "Serena", "Uncle_fu", "Dylan", "Eric",
    "Ryan", "Aiden", "Ono_anna", "Sohee",
]
LANGUAGES = [
    "Auto", "Chinese", "English", "Japanese", "Korean",
    "French", "German", "Spanish", "Portuguese", "Russian", "Italian",
]
MODEL_SIZES = ("0.6B", "1.7B")


class VoiceService:
    """ttsclone 子进程封装：合成（custom）/ 转写（transcribe）/ 状态探测。"""

    def __init__(
        self,
        tts_dir: str | None = None,
        speaker: str = "Vivian",
        model_size: str = "1.7B",
        timeout: int = 600,
        output_dir: str | None = None,
    ):
        base = Path(__file__).resolve().parents[2]  # backend/
        dir_value = tts_dir or settings.voice_tts_dir
        self.tts_dir = Path(dir_value)
        if not self.tts_dir.is_absolute():
            # 相对路径以 backend 为基准（默认 "../ttsclone" → 仓库根/ttsclone）
            self.tts_dir = (base / self.tts_dir).resolve()
        self.speaker = speaker if speaker in SPEAKERS else "Vivian"
        self.model_size = model_size if model_size in MODEL_SIZES else "1.7B"
        self.timeout = timeout
        self.output_dir = Path(output_dir or (base / "data" / "generated"))

    @property
    def enabled(self) -> bool:
        """总开关：配置启用 且 clone.py 存在。"""
        return bool(settings.voice_tts_enabled) and self._clone_script().exists()

    def _clone_script(self) -> Path:
        return self.tts_dir / "clone.py"

    def _python(self) -> str:
        venv = self.tts_dir / ".venv" / "Scripts" / "python.exe"
        if venv.exists():
            return str(venv)
        return "python"

    def _run(self, args: list[str], timeout: int | None = None) -> tuple[bool, dict]:
        """运行 ttsclone CLI，返回 (ok, result)；result 含 error/text/path/output。"""
        if not self._clone_script().exists():
            return False, {"error": f"ttsclone not found: {self.tts_dir}"}
        cmd = [self._python(), str(self._clone_script())] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                cwd=str(self.tts_dir),
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return False, {"error": f"python not found: {self._python()}"}
        except subprocess.TimeoutExpired:
            return False, {"error": f"timed out after {timeout or self.timeout}s"}
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, {"error": stderr or stdout or "process exited with error"}
        # clone.py 会在 stdout 打印一行 JSON 结果
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    return bool(data.get("ok", True)), data
                except json.JSONDecodeError:
                    continue
        return True, {"output": stdout}

    def synthesize(
        self,
        text: str,
        speaker: str = "",
        language: str = "Auto",
        instruct: str = "",
        model_size: str = "",
    ) -> tuple[bool, str, Path | None]:
        """预设音色合成，写入 output_dir，返回 (ok, message, path)。"""
        spk = speaker if speaker in SPEAKERS else self.speaker
        ms = model_size if model_size in MODEL_SIZES else self.model_size
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        outfile = self.output_dir / f"tts_{spk}_{ts}.wav"
        args = ["custom", text.strip(), "--speaker", spk, "--lang", language, "--output", str(outfile)]
        if instruct and instruct.strip():
            args += ["--instruct", instruct.strip()]
        if ms == "0.6B":
            args.append("--small")
        ok, res = self._run(args)
        if not ok:
            return False, str(res.get("error", "synthesis failed")), None
        path = Path(str(res.get("path", outfile)))
        if path.exists() and path.stat().st_size > 0:
            return True, str(path), path
        return True, str(outfile), outfile

    def transcribe(self, audio_path: str) -> tuple[bool, str]:
        """Whisper 转写音频为文本，返回 (ok, text)。"""
        if not Path(audio_path).exists():
            return False, "audio file not found"
        ok, res = self._run(["transcribe", audio_path])
        if not ok:
            return False, str(res.get("error", "transcribe failed"))
        text = str(res.get("text") or res.get("output") or "").strip()
        return True, text


def create_voice_service() -> VoiceService:
    """构造运行时共享的语音服务（按 settings 注入）。"""
    return VoiceService()
