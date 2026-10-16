"""
File Generator Utility

Simple utility to save generated files to data/generated/ directory.
Other modules import and call save_file() to persist their output.
"""
from datetime import datetime
from pathlib import Path


GENERATED_DIR = Path(__file__).resolve().parents[2] / "data" / "generated"


def save_file(content: str | bytes, filename: str = "") -> str:
    """将内容保存到生成文件目录，返回文件路径。"""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"file_{ts}"

    # 仅保留 basename，防止路径穿越（filename="../../.env" 会被截断为 ".env"）
    name = Path(filename).name
    if not name or name in (".", ".."):
        name = f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    filepath = GENERATED_DIR / name
    if not filepath.resolve().is_relative_to(GENERATED_DIR.resolve()):
        raise ValueError(f"filename must stay inside {GENERATED_DIR}")

    if isinstance(content, str):
        filepath.write_text(content, encoding="utf-8")
    else:
        filepath.write_bytes(content)

    return str(filepath)
