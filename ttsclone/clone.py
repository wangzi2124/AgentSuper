#!/usr/bin/env python3
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
    """啟動時下載所有需要的模型到本地目錄"""
    print("=" * 60)
    print("檢查模型下載狀態...")
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
    """載入模型（使用本地模型）"""
    from qwen_tts import Qwen3TTSModel

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

    local_path = os.path.join(LOCAL_MODELS_DIR, model_id.replace("/", "_"))
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


def main():
    download_all_models()
    parser = argparse.ArgumentParser(
        description="Qwen3-TTS 聲音複製 CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
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

    args = parser.parse_args()

    if args.command == "custom":
        cmd_custom(args)
    elif args.command == "clone":
        cmd_clone(args)


if __name__ == "__main__":
    main()
