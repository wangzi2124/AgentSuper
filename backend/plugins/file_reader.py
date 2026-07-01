"""
File Reader Plugin

Reads files from the uploads/generated directories.
Images are returned as base64 for multimodal LLM support.
"""
import base64
import os
from pathlib import Path

PLUGIN_NAME = "file-reader"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Read files from the knowledge base (uploads/generated directories)"


def _resolve_path(filename: str) -> str | None:
    for base in ["data/uploads", "data/generated", "data"]:
        p = Path(os.getcwd()) / base / filename
        if p.exists() and p.is_file():
            return str(p)
    return None


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log"}
_PDF_EXT = {".pdf"}


def tool_read_file(filename: str) -> str:
    """Read a file from the knowledge base directories (uploads/generated/data).

    For images (png/jpg/jpeg/gif/webp), returns a base64 data URI that can be
    passed to multimodal models. For text files, returns the raw content.
    For PDFs, returns the raw bytes as base64.

    Parameters:
    - filename: name of the file (with extension)
    """
    path = _resolve_path(filename)
    if not path:
        return f"Error: file '{filename}' not found in uploads, generated, or data directories"

    ext = Path(path).suffix.lower()

    try:
        if ext in _IMAGE_EXTS:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(ext, "image/png")
            return f"data:{mime};base64,{b64}"

        elif ext in _TEXT_EXTS:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()

        elif ext in _PDF_EXT:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            return f"data:application/pdf;base64,{b64}"

        else:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            return f"data:application/octet-stream;base64,{b64}"

    except Exception as e:
        return f"Error reading '{filename}': {e}"
