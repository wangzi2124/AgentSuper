"""
File Generator Utility

Simple utility to save generated files to data/generated/ directory.
Other plugins import and call save_file() to persist their output.
"""
from datetime import datetime
from pathlib import Path


GENERATED_DIR = Path(__file__).resolve().parents[1] / "data" / "generated"


def save_file(content: str | bytes, filename: str = "") -> str:
    """
    Save content to data/generated/ directory.

    Args:
        content: text (str) or binary (bytes) content to save
        filename: desired filename (auto-generated if empty)

    Returns:
        absolute path to the saved file
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"file_{ts}"

    filepath = GENERATED_DIR / filename

    if isinstance(content, str):
        filepath.write_text(content, encoding="utf-8")
    else:
        filepath.write_bytes(content)

    return str(filepath)
