"""拆分模块 `common`（含 DEFAULT_READ_LIMIT、MAX_BYTES、MAX_BYTES_LABEL、MAX_LINE_LENGTH、MAX_LINE_SUFFIX、SAMPLE_BYTES、_AUDIO_EXTS、_BINARY_EXTS、_DOC_EXTS、_IMAGE_EXTS、_MIME_MAP、_MULTIMODAL_EXTS、_PDF_EXTS、_TEXT_EXTS、_VIDEO_EXTS、_coerce_bool、_coerce_int、_env、unwrap）。

原文件 docstring: (无)"""

# ── 复制自原模块的顶层 import ──

import base64

import json

import os

import re

import shlex

import shutil

import signal

import stat

import subprocess

import time

from datetime import datetime

from pathlib import Path

from typing import Optional

from app.filesystem import GitignoreMatcher, ScanCache, get_project, glob_to_regex

from app.permission import get_manager as get_perm_mgr, NeedsPermission, current_session_workspace

# ── 拆分内语句（verbatim，含前置注释，保持原始顺序）──

def _env(title: str, output: str, **metadata) -> dict:
    """工具结果信封（对齐 opencode Tool.execute 返回的 {title, metadata, output}）。

    调用方（graph._execute_tool / sub_tools.run_tool）用 unwrap() 提取 output 喂给 LLM；
    信封中的 metadata 可承载 preview/display 等结构化信息供前端展示。
    """
    return {"title": title, "metadata": metadata, "output": output}

def unwrap(result: object) -> str:
    """从信封结构提取 output 字符串；非信封直接 str()（兼容旧返回）。"""
    if isinstance(result, dict) and "output" in result:
        return str(result["output"])
    return str(result)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}

_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log", ".py", ".js", ".ts", ".vue", ".html", ".css", ".scss", ".less", ".sh", ".bat", ".ps1", ".env", ".env.example", ".ini", ".cfg", ".conf", ".toml", ".sql", ".sqlite"}

_PDF_EXTS = {".pdf"}

_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}

_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

_DOC_EXTS = {".docx", ".xlsx", ".pptx"}

_MULTIMODAL_EXTS = _IMAGE_EXTS | _PDF_EXTS | _AUDIO_EXTS | _VIDEO_EXTS

_MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

DEFAULT_READ_LIMIT = 2000

MAX_LINE_LENGTH = 2000

MAX_LINE_SUFFIX = f"... (line truncated to {MAX_LINE_LENGTH} chars)"

MAX_BYTES = 50 * 1024

MAX_BYTES_LABEL = f"{MAX_BYTES // 1024} KB"

SAMPLE_BYTES = 4096

_BINARY_EXTS = frozenset({
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar", ".war",
    ".7z", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp", ".bin", ".dat",
    ".obj", ".o", ".a", ".lib", ".wasm", ".pyc", ".pyo",
})

def _coerce_int(value, default: int = 0) -> int:
    """将任意输入安全转换为整数（LLM 可能以字符串形式传数值参数）。

    布尔值不是有效整数（True 在 int 强转下为 1），按默认值处理。
    """
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _coerce_bool(value, default: bool = False) -> bool:
    """将任意输入安全转换为布尔值，容忍 "true"/"1"/"yes" 等字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)



__all__ = ["DEFAULT_READ_LIMIT", "MAX_BYTES", "MAX_BYTES_LABEL", "MAX_LINE_LENGTH", "MAX_LINE_SUFFIX", "SAMPLE_BYTES", "_AUDIO_EXTS", "_BINARY_EXTS", "_DOC_EXTS", "_IMAGE_EXTS", "_MIME_MAP", "_MULTIMODAL_EXTS", "_PDF_EXTS", "_TEXT_EXTS", "_VIDEO_EXTS", "_coerce_bool", "_coerce_int", "_env", "unwrap"]
