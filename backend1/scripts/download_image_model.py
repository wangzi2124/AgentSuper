# -*- coding: utf-8 -*-
"""预下载图片 caption 视觉模型（默认 ollama/llava），**不自动下载**。

- IMAGE_CAPTION_MODEL=ollama/llava（内网/离线推荐）→ 本脚本执行 `ollama pull llava`。
- IMAGE_CAPTION_MODEL=openai/gpt-4o-mini（公网 API）→ 无需本地下载，仅提示。

用法：
    .venv\\Scripts\\python.exe scripts/download_image_model.py
"""
import shutil
import subprocess
import sys

DEFAULT_MODEL = "llava"


def _ollama_exists() -> bool:
    return shutil.which("ollama") is not None


def main():
    print(f"[DL] 图片 caption 视觉模型: ollama/{DEFAULT_MODEL}")
    if not _ollama_exists():
        print(
            "[FAIL] 未找到 ollama 命令（需先安装 https://ollama.com）。\n"
            "       或改用公网 API 模型（无需下载）：在 .env 设置 "
            "IMAGE_CAPTION_MODEL=openai/gpt-4o-mini + OPENAI_API_KEY"
        )
        return 1
    try:
        subprocess.run(["ollama", "pull", DEFAULT_MODEL], check=True)
        subprocess.run(["ollama", "list"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] ollama pull 失败: {e}")
        return 1
    print(
        "完成。然后在 backend/.env 设置：\n"
        "  IMAGE_CAPTION_MODEL=ollama/llava\n"
        "  IMAGE_CAPTION_API_BASE=http://localhost:11434"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
