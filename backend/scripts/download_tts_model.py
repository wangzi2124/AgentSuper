# -*- coding: utf-8 -*-
"""预下载 Qwen3-TTS 模型到 backend/ttsclone/models（手动/离线备用）。

默认链路：VOICE_TTS_ENABLED=true 时服务启动会自动下载（后台线程，同向量模型），
本脚本用于手动/离线预下载或补全其它规格模型。
ModelScope 优先 / HuggingFace 回退（复用 app/utils/model_download.py 的断点续传/重试/超时）。

用法：
    .venv\\Scripts\\python.exe scripts/download_tts_model.py            # 默认 1.7B CustomVoice
    ... --size 0.6B                                                   # 0.6B CustomVoice
    ... --all                                                          # 全部 5 个模型
    ... --whisper                                                      # 另预下载 Whisper turbo（转写用）
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.utils.model_download import download_model  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
MODELS_DIR = BACKEND / "ttsclone" / "models"

ALL_MODELS = [
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
]


def _flat(model: str) -> Path:
    return MODELS_DIR / model.replace("/", "_")


def _ensure_flat(path: Path, model: str) -> Path:
    """download_model 可能返回 ModelScope 嵌套布局，统一到 ttsclone 期望的扁平命名。"""
    flat = _flat(model)
    if flat == path or (flat.exists() and any(flat.iterdir())):
        return flat
    flat.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if flat.exists():
            shutil.rmtree(flat)
        os.replace(str(path), str(flat))
    return flat


def download_one(model: str) -> Path:
    print(f"[DL] {model}")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    flat = _flat(model)
    if flat.exists() and any(flat.iterdir()):
        print(f"[OK] 已存在: {flat}")
        return flat
    p = download_model(model, cache_dir=MODELS_DIR)
    flat = _ensure_flat(p, model)
    print(f"[OK] 已下载至: {flat}")
    return flat


def download_whisper() -> None:
    print("[DL] Whisper turbo（ASR 转写用）")
    try:
        import whisper
    except ImportError:
        print("[FAIL] 未安装 openai-whisper（ttsclone 环境安装后重试）")
        return
    from whisper import _download as _dl

    root = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
    os.makedirs(root, exist_ok=True)
    target = os.path.join(root, "turbo.pt")
    if os.path.exists(target):
        print(f"[OK] 已存在: {target}")
        return
    _dl(whisper._MODELS["turbo"], root, False)
    print(f"[OK] 已下载: {target}")


def main():
    ap = argparse.ArgumentParser(description="预下载 Qwen3-TTS 模型（同向量模型：ModelScope 优先 / HF 回退）")
    ap.add_argument("--size", choices=["0.6B", "1.7B"], default="1.7B")
    ap.add_argument("--all", action="store_true", help="下载全部 5 个模型")
    ap.add_argument("--whisper", action="store_true", help="另预下载 Whisper turbo（转写）")
    args = ap.parse_args()

    models = ALL_MODELS if args.all else [f"Qwen/Qwen3-TTS-12Hz-{args.size}-CustomVoice"]
    for m in models:
        try:
            download_one(m)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] 跳过 {m}: {e}")
    if args.whisper:
        download_whisper()
    print(f"完成。模型目录: {MODELS_DIR}")
    print("然后设置 backend/.env: VOICE_TTS_ENABLED=true")


if __name__ == "__main__":
    main()
