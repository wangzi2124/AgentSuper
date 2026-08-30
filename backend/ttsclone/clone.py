#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3-TTS 聲音複製 CLI 工具
用法：
    # 使用預設角色聲音
    uv run clone.py custom "你好，世界！" --speaker Vivian --instruct "用開心的語氣"

    # 複製聲音
    uv run clone.py clone "你好，世界！" --ref-audio ref_audio/sample.wav --ref-text "參考音訊文字"

    # 使用 0.6B 小模型（12GB VRAM 建議）
    uv run clone.py custom "你好！" --speaker Serena --small

    # 指定語言
    uv run clone.py clone "Hello world!" --ref-audio ref.wav --lang English
"""

import argparse
import datetime
import json
import os
import sys

import torch
import soundfile as sf
from huggingface_hub import snapshot_download

LOCAL_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

REQUIRED_MODELS = [
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
]


def download_all_models():
    """显式下载所有模型到本地目录（仅 --download 时调用，不自动触发）。

    模型文件由后端脚本 scripts/download_tts_model.py 预下载（ModelScope 优先/HF 回退，
    对齐向量/嵌入模型下载方式）。此处保留 CLI 入口供 ttsclone 独立使用。
    """
    print("=" * 60)
    print("下载 Qwen3-TTS 模型（可由 backend/scripts/download_tts_model.py 预下载）")
    print("=" * 60)

    for model_id in REQUIRED_MODELS:
        local_path = os.path.join(LOCAL_MODELS_DIR, model_id.replace("/", "_"))

        if os.path.exists(local_path) and os.path.isdir(local_path):
            print(f"[OK] 已存在: {model_id}")
            continue

        print(f"[DL] 正在下載: {model_id}")
        try:
            snapshot_download(
                repo_id=model_id,
                local_dir=local_path,
            )
            print(f"  已下載至: {local_path}")
        except Exception as e:
            print(f"  下載失敗: {e}")

    print("=" * 60)
    print("模型下載完成！")
    print("=" * 60)


OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_model(model_id: str):
    """載入模型（使用本地模型，不自動下載）"""
    from qwen_tts import Qwen3TTSModel

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

    local_path = os.path.join(LOCAL_MODELS_DIR, model_id.replace("/", "_"))
    if not (os.path.exists(local_path) and os.path.isdir(local_path)):
        raise FileNotFoundError(
            f"模型不存在: {local_path}\n"
            f"请先运行 backend/scripts/download_tts_model.py 预下载模型（模型不会自动下载）。"
        )
    print(f"正在載入模型: {local_path} ...")
    print(f"裝置: {device} | 精度: {dtype}")

    model = Qwen3TTSModel.from_pretrained(
        local_path,
        device_map=device,
        dtype=dtype,
        attn_implementation="sdpa",
    )
    print("模型載入完成！")
    return model


def generate_timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def cmd_custom(args):
    """預設角色語音合成"""
    size = "0.6B" if args.small else "1.7B"
    model_id = f"Qwen/Qwen3-TTS-12Hz-{size}-CustomVoice"
    model = get_model(model_id)

    print(f"角色: {args.speaker} | 語言: {args.lang} | 指令: {args.instruct or '無'}")
    print(f"文字: {args.text}")

    wavs, sr = model.generate_custom_voice(
        text=args.text,
        language=args.lang,
        speaker=args.speaker,
        instruct=args.instruct or "",
    )

    outfile = args.output or os.path.join(
        OUTPUT_DIR, f"custom_{args.speaker}_{generate_timestamp()}.wav"
    )
    sf.write(outfile, wavs[0], sr)
    print(f"已儲存至: {outfile}")


def cmd_clone(args):
    """聲音複製"""
    size = "0.6B" if args.small else "1.7B"
    model_id = f"Qwen/Qwen3-TTS-12Hz-{size}-Base"
    model = get_model(model_id)

    print(f"參考音訊: {args.ref_audio}")
    print(f"語言: {args.lang}")
    print(f"文字: {args.text}")

    kwargs = {
        "text": args.text,
        "language": args.lang,
        "ref_audio": args.ref_audio,
    }
    if args.ref_text:
        kwargs["ref_text"] = args.ref_text

    wavs, sr = model.generate_voice_clone(**kwargs)

    outfile = args.output or os.path.join(
        OUTPUT_DIR, f"clone_{generate_timestamp()}.wav"
    )
    sf.write(outfile, wavs[0], sr)
    print(f"已儲存至: {outfile}")


def cmd_transcribe(args):
    """Whisper 转写音频为文本（输出最后一行 JSON：{"ok": true, "text": "..."}）"""
    import soundfile as _sf
    import whisper

    # 旧 whisper 权重含自定义类，torch>=2.6 默认 weights_only=True 会失败 → 回退
    import torch as _torch
    _orig_load = _torch.load
    def _legacy_load(*a, **k):
        k["weights_only"] = False
        return _orig_load(*a, **k)
    _torch.load = _legacy_load

    # 不随运行自动下载 whisper：缺失时明确报错，引导安装/预下载步骤
    _target = os.path.join(os.path.expanduser("~"), ".cache", "whisper", "large-v3-turbo.pt")
    if not os.path.exists(_target):
        print(json.dumps({
            "ok": False,
            "error": "Whisper 模型未下载（large-v3-turbo.pt）。"
                     "请先执行 backend/scripts/download_tts_model.py --whisper 预下载。",
        }, ensure_ascii=False))
        return

    # 解码音频：优先 whisper.load_audio（内部走 ffmpeg，可解码 webm/mp3/m4a/ogg/wav 等任意格式——
    # 浏览器 MediaRecorder 录音是 webm/opus，soundfile/libsndfile 解析不了 webm，会致 503）。
    # ffmpeg 缺失时退化 soundfile（仅 wav/flac 等受支持格式）。
    try:
        wav = whisper.load_audio(args.audio)  # float32 mono 16k
        sr = 16000
    except Exception as load_err:
        import subprocess as _sp
        if _sp.run(["ffmpeg", "-version"], capture_output=True).returncode != 0:
            wav, sr = _sf.read(args.audio, dtype="float32")
        else:
            raise
    # whisper 内部自带重采样，无需手动 resample（手动 interpolate 曾引入坏样本 → 转写乱码）
    device = "cuda" if args.device == "auto" and _torch.cuda.is_available() else ("cpu" if args.device == "auto" else args.device)
    model = whisper.load_model("turbo", device=device)
    result = model.transcribe(wav, fp16=(device == "cuda"))
    print(json.dumps({"ok": True, "text": result["text"].strip()}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="Qwen3-TTS 聲音複製 CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--download", action="store_true",
                        help="预下载全部模型（默认不自动下载；也可用 backend/scripts/download_tts_model.py）")
    sub = parser.add_subparsers(dest="command", required=True)

    # custom 子命令
    p_custom = sub.add_parser("custom", help="使用預設角色聲音合成")
    p_custom.add_argument("text", help="要合成的文字")
    p_custom.add_argument("--speaker", "-s", default="Vivian",
                          choices=["Vivian", "Serena", "Uncle_Fu", "Dylan",
                                   "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"],
                          help="角色名稱（預設: Vivian）")
    p_custom.add_argument("--instruct", "-i", default="",
                          help="情緒/風格指令，例如「用開心的語氣」")
    p_custom.add_argument("--lang", "-l", default="Auto", help="語言（預設: Auto）")
    p_custom.add_argument("--output", "-o", help="輸出檔案路徑")
    p_custom.add_argument("--small", action="store_true",
                          help="使用 0.6B 小模型（VRAM 不足時使用）")

    # clone 子命令
    p_clone = sub.add_parser("clone", help="用參考音訊複製聲音")
    p_clone.add_argument("text", help="要合成的文字")
    p_clone.add_argument("--ref-audio", "-r", required=True,
                         help="參考音訊檔案路徑（5~15秒最佳）")
    p_clone.add_argument("--ref-text", "-t", default="",
                         help="參考音訊的文字內容（提供可提升品質）")
    p_clone.add_argument("--lang", "-l", default="Auto", help="語言（預設: Auto）")
    p_clone.add_argument("--output", "-o", help="輸出檔案路徑")
    p_clone.add_argument("--small", action="store_true",
                         help="使用 0.6B 小模型（VRAM 不足時使用）")

    # transcribe 子命令：Whisper 转写（ASR），供后端 /api/voice/transcribe 调用
    p_tr = sub.add_parser("transcribe", help="Whisper 转写音频为文本")
    p_tr.add_argument("audio", help="音频文件路径（wav/mp3/flac 等）")
    p_tr.add_argument("--device", default="auto",
                      help="auto（有 GPU 用 cuda）/ cpu / cuda")

    args = parser.parse_args()

    if args.download:
        download_all_models()

    if args.command == "custom":
        cmd_custom(args)
    elif args.command == "clone":
        cmd_clone(args)
    elif args.command == "transcribe":
        cmd_transcribe(args)


if __name__ == "__main__":
    main()
