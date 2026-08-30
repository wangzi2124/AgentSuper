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
import os
import queue
import shutil
import subprocess
import sys
import threading
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


class _WhisperWorker:
    """常驻 Whisper 转写子进程（`clone.py transcribe-serve`），模型只加载一次。

    增量转写/多请求复用同一进程，避免每次转写重载 ~1.6GB 模型（本机实测单次含加载 ~54s）。
    json 行协议：stdin 每行 {"path": ...} → stdout 每行 {"ok":..,"text"|"error":..}。
    请求串行（threading.Lock）；崩溃/超时自动杀进程重启；父进程死亡 → stdin EOF → 子进程自退。
    """

    def __init__(self, service: "VoiceService", timeout: int):
        self.svc = service
        self.timeout = timeout
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._out_q: queue.Queue = queue.Queue()

    def _spawn(self) -> None:
        self._out_q = queue.Queue()
        cmd = [self.svc._python(), str(self.svc._clone_script()), "transcribe-serve"]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # tqdm 进度等噪声丢弃，避免管道填塞
            cwd=str(self.svc.tts_dir),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self._reader = threading.Thread(
            target=_WhisperWorker._read_loop, args=(self._out_q, self._proc), daemon=True,
        )
        self._reader.start()

    @staticmethod
    def _read_loop(out_q: queue.Queue, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                out_q.put(line.decode("utf-8", "replace").strip())
        except Exception:  # noqa: BLE001
            pass
        finally:
            out_q.put(None)  # EOF 哨兵

    def transcribe(self, audio_path: str) -> tuple[bool, str]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._stop_unlocked()
                self._spawn()
            try:
                self._proc.stdin.write(
                    (json.dumps({"path": audio_path}) + "\n").encode("utf-8"),
                )
                self._proc.stdin.flush()
                line = self._out_q.get(timeout=self.timeout)
                if line is None:
                    raise RuntimeError("whisper worker exited unexpectedly")
                data = json.loads(line)
                ok = bool(data.get("ok", False))
                return ok, str(data.get("text", "") if ok else data.get("error", ""))
            except queue.Empty:
                self._stop_unlocked()
                return False, f"whisper worker timed out after {self.timeout}s"
            except Exception as e:  # noqa: BLE001
                self._stop_unlocked()
                return False, f"whisper worker error: {e}"

    def _stop_unlocked(self) -> None:
        if self._proc is None:
            return
        try:
            try:
                self._proc.stdin.write(b"shutdown\n")
                self._proc.stdin.flush()
            except Exception:  # noqa: BLE001
                pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._proc = None
            self._reader = None

    def shutdown(self) -> None:
        with self._lock:
            self._stop_unlocked()


WHISPER_MODEL_FILE = "small.pt"
WHISPER_VARIANT = "small"


class VoiceService:
    """ttsclone 子进程封装：合成（custom）/ 转写（transcribe）/ 状态探测。"""

    def __init__(
        self,
        tts_dir: str | Path | None = None,
        speaker: str | None = None,
        model_size: str | None = None,
        timeout: int | None = None,
        output_dir: str | None = None,
    ):
        base = Path(__file__).resolve().parents[2]  # backend/
        # 目录固定为 backend/ttsclone，不配置路径（测试可显式传入临时目录）
        self.tts_dir = Path(tts_dir) if tts_dir else (base / "ttsclone")
        # 未显式传入时取 Settings（.env：VOICE_TTS_SPEAKER / VOICE_TTS_MODEL_SIZE /
        # VOICE_TTS_TIMEOUT），避免创建方随手 new VoiceService() 而错过配置。
        self.speaker = (speaker or settings.voice_tts_speaker)
        if self.speaker not in SPEAKERS:
            self.speaker = "Vivian"
        self.model_size = (model_size or settings.voice_tts_model_size)
        if self.model_size not in MODEL_SIZES:
            self.model_size = "1.7B"
        self.timeout = timeout or settings.voice_tts_timeout
        self.output_dir = Path(output_dir or (base / "data" / "generated"))
        self._whisper_worker: _WhisperWorker | None = None
        self._whisper_ready_val: bool | None = None

    @property
    def model_id(self) -> str:
        """当前配置模型规格对应的模型仓库 id。"""
        return f"Qwen/Qwen3-TTS-12Hz-{self.model_size}-CustomVoice"

    @property
    def model_path(self) -> Path:
        """当前配置模型规格对应的本地模型目录（ttsclone/models 下扁平命名）。"""
        return self.tts_dir / "models" / self.model_id.replace("/", "_")

    @property
    def has_model(self) -> bool:
        """模型是否已就绪（不自动下载已由启动 ensure_models 处理）。"""
        return self.model_path.is_dir() and any(self.model_path.iterdir())

    @property
    def enabled(self) -> bool:
        """总开关：配置启用 且 clone.py 存在 且 模型已下载。"""
        return bool(settings.voice_tts_enabled) and self._clone_script().exists() and self.has_model

    def _ensure_flat(self, path: Path) -> Path:
        """download_model 可能返回 ModelScope 嵌套布局，统一到 ttsclone 期望的扁平命名。"""
        flat = self.tts_dir / "models" / self.model_id.replace("/", "_")
        if flat == path or (flat.exists() and any(flat.iterdir())):
            return flat
        flat.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if flat.exists():
                shutil.rmtree(flat)
            os.replace(str(path), str(flat))
        return flat

    def _ensure_whisper(self) -> None:
        """（预下载工具专用，启动不再调用）尽力而为预下载 faster-whisper-small + Whisper small；失败仅警告。"""
        fw_dir = self.tts_dir / "models" / "faster-whisper-small"
        try:
            if not (fw_dir / "model.bin").exists():
                from huggingface_hub import snapshot_download
                fw_dir.parent.mkdir(parents=True, exist_ok=True)
                snapshot_download("Systran/faster-whisper-small", local_dir=str(fw_dir))
                logger.info("[voice] faster-whisper-small 已下载: %s", fw_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning("[voice] faster-whisper-small 下载失败（转写将回退 openai-whisper small）: %s", e)
        try:
            import whisper
            root = Path.home() / ".cache" / "whisper"
            target = root / WHISPER_MODEL_FILE
            if target.exists():
                return
            root.mkdir(parents=True, exist_ok=True)
            from whisper import _download as _dl
            _dl(whisper._MODELS[WHISPER_VARIANT], str(root), False)
            logger.info("[voice] Whisper %s 已下载: %s", WHISPER_VARIANT, target)
        except Exception as e:  # noqa: BLE001
            logger.warning("[voice] openai-whisper 下载失败: %s", e)

    def ensure_models(self) -> None:
        """启动时预下载 TTS 合成模型（同向量/嵌入模型：ModelScope 优先 / HF 回退，断点续传）。

        不抛异常：下载失败仅降级（语音端点返回 503 并引导手动脚本），不阻断服务启动。
        后台线程调用（runtime.py），已就绪时直接跳过。
        Whisper（转写用）不再随启动下载——并入安装/预下载步骤
        （pip install -r requirements-voice.txt 后 python scripts/download_tts_model.py --whisper）。
        """
        if self.has_model:
            logger.info("[voice] 模型已就绪: %s", self.model_path)
            return
        try:
            from app.utils.model_download import download_model
            models_dir = self.tts_dir / "models"
            models_dir.mkdir(parents=True, exist_ok=True)
            logger.info("[voice] 启动下载模型 %s → %s", self.model_id, models_dir)
            p = download_model(self.model_id, cache_dir=models_dir)
            self._ensure_flat(p)
            logger.info("[voice] 模型下载完成: %s", self.model_path)
        except Exception as e:  # noqa: BLE001 —— 下载失败降级，不阻断启动
            logger.warning(
                "[voice] 模型下载失败，语音功能降级（可稍后手动运行 "
                "scripts/download_tts_model.py）：%s", e,
            )

    def _clone_script(self) -> Path:
        return self.tts_dir / "clone.py"

    def _python(self) -> str:
        """全部依赖已安装到后端 venv（不再使用 ttsclone 独立 .venv）。"""
        return sys.executable

    def _run(self, args: list[str], timeout: int | None = None) -> tuple[bool, dict]:
        """运行 ttsclone CLI，返回 (ok, result)；result 含 error/text/path/output。"""
        if not self._clone_script().exists():
            return False, {"error": f"ttsclone not found: {self.tts_dir}"}
        cmd = [self._python(), str(self._clone_script())] + args
        # 子进程 stdout 强制 utf-8：Windows 下默认 GBK 会把 json.dumps 中文写成 GBK 字节，
        # 父进程按 utf-8 解码即乱码。
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                cwd=str(self.tts_dir),
                encoding="utf-8",
                errors="replace",
                env=env,
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
        """Whisper 转写音频为文本。走常驻 worker（模型只加载一次，增量/多次调用快）。"""
        if not Path(audio_path).exists():
            return False, "audio file not found"
        if not self.whisper_ready:
            return False, (
                "增量转写引擎不可用（backend/ttsclone/models/faster-whisper-small 或 "
                "~/.cache/whisper/small.pt 未下载）。请先执行 "
                "backend/scripts/download_tts_model.py --whisper 预下载。"
            )
        if self._whisper_worker is None:
            self._whisper_worker = _WhisperWorker(self, self.timeout)
        return self._whisper_worker.transcribe(str(audio_path))

    def shutdown(self) -> None:
        """终止常驻 Whisper 子进程（服务关闭时调用，避免遗留进程）。"""
        if self._whisper_worker is not None:
            self._whisper_worker.shutdown()
            self._whisper_worker = None

    def warmup(self) -> None:
        """后台预热常驻 Whisper worker：拉起子进程让模型（small）在后台加载完成。

        由此把首次转写的模型加载成本（本机 ~5.7s）从用户首次点麦克风挪到服务启动阶段，
        录音一开始就能以全速（~2s/块）出字。子进程会在读 stdin 前先加载模型，
        因此仅 spawn 即可触发加载；失败静默（下次 transcribe 时仍会自动重建 worker）。
        """
        if not self.whisper_ready or self._whisper_worker is not None:
            return
        try:
            w = _WhisperWorker(self, self.timeout)
            w._spawn()  # 子进程后台加载模型，返回即开工
            self._whisper_worker = w
            logger.info("[voice] Whisper worker 预热启动（模型后台加载中）")
        except Exception as e:  # noqa: BLE001
            logger.warning("[voice] Whisper worker 预热失败（下次转写自动重建）: %s", e)
            try:
                w.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._whisper_worker = None

    @property
    def whisper_ready(self) -> bool:
        """增量转写引擎是否就绪：faster-whisper-small(ct2) 优先，回退 openai-whisper small.pt。"""
        if self._whisper_ready_val is None:
            ok = False
            # faster-whisper（首选）：模型目录 + 依赖
            if (self.tts_dir / "models" / "faster-whisper-small" / "model.bin").exists():
                try:
                    import faster_whisper  # noqa: F401
                    ok = True
                except ImportError:
                    ok = False
            # openai-whisper small（回退）
            if not ok:
                try:
                    import whisper  # noqa: F401
                    ok = (Path.home() / ".cache" / "whisper" / WHISPER_MODEL_FILE).exists()
                except ImportError:
                    ok = False
            self._whisper_ready_val = ok
        return self._whisper_ready_val


def create_voice_service() -> VoiceService:
    """构造运行时共享的语音服务（按 settings 注入）。"""
    return VoiceService()
