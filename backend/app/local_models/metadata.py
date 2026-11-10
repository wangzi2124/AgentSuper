"""GGUF metadata extraction and catalog entry synthesis.

The GGUF header is parsed best-effort with a bounded read: we only need the
small `general.*` / `<arch>.*` scalars, which normally precede the large
tokenizer arrays. Anything we cannot reach within the cap falls back to
filename heuristics.
"""

from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path
from typing import Any

from app.local_models.sources import parameter_size_of, quantization_of

_MAX_HEADER_BYTES = 16 * 1024 * 1024
_GENERIC_NAMES = {"llama", "model", "gguf", "llm", "unknown", "test"}

_SCALAR_SIZES = {
    0: 1,   # uint8
    1: 1,   # int8
    2: 2,   # uint16
    3: 2,   # int16
    4: 4,   # uint32
    5: 4,   # int32
    6: 4,   # float32
    7: 1,   # bool
    10: 8,  # uint64
    11: 8,  # int64
    12: 8,  # float64
}


class _Truncated(Exception):
    pass


class _Buf:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.i = 0

    def read(self, n: int) -> bytes:
        if n < 0 or self.i + n > len(self.data):
            raise _Truncated
        chunk = self.data[self.i:self.i + n]
        self.i += n
        return chunk

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def skip(self, n: int) -> None:
        if n < 0 or self.i + n > len(self.data):
            raise _Truncated
        self.i += n


def _read_string(buf: _Buf) -> str:
    n = buf.u64()
    if n > len(buf.data) - buf.i:
        raise _Truncated
    return buf.read(n).decode("utf-8", "replace")


def _read_value(buf: _Buf, vtype: int) -> Any:
    if vtype in _SCALAR_SIZES:
        raw = buf.read(_SCALAR_SIZES[vtype])
        if vtype == 0:
            return raw[0]
        if vtype == 1:
            return struct.unpack("<b", raw)[0]
        if vtype == 2:
            return struct.unpack("<H", raw)[0]
        if vtype == 3:
            return struct.unpack("<h", raw)[0]
        if vtype == 4:
            return struct.unpack("<I", raw)[0]
        if vtype == 5:
            return struct.unpack("<i", raw)[0]
        if vtype == 6:
            return struct.unpack("<f", raw)[0]
        if vtype == 7:
            return bool(raw[0])
        if vtype == 10:
            return struct.unpack("<Q", raw)[0]
        if vtype == 11:
            return struct.unpack("<q", raw)[0]
        if vtype == 12:
            return struct.unpack("<d", raw)[0]
    if vtype == 8:
        return _read_string(buf)
    if vtype == 9:
        elem_type = buf.u32()
        count = buf.u64()
        if elem_type in _SCALAR_SIZES:
            buf.skip(_SCALAR_SIZES[elem_type] * count)
        else:
            for _ in range(count):
                _read_value(buf, elem_type)
        return None
    return None


def parse_gguf_metadata(path: str | Path, max_bytes: int = _MAX_HEADER_BYTES) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes)
    except OSError:
        return {}
    if len(data) < 24 or data[:4] != b"GGUF":
        return {}
    buf = _Buf(data)
    buf.skip(4)
    try:
        version = buf.u32()
        if version < 2:
            return {}
        buf.u64()  # tensor_count
        kv_count = buf.u64()
        meta: dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(buf)
            vtype = buf.u32()
            meta[key] = _read_value(buf, vtype)
        return meta
    except (_Truncated, struct.error, ValueError):
        return meta if "meta" in locals() else {}


def _first_int(meta: dict[str, Any], keys: list[str]) -> int:
    for key in keys:
        val = meta.get(key)
        if isinstance(val, (int, float)) and int(val) > 0:
            return int(val)
    return 0


def sanitize_model_name(text: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", (text or "").strip().lower()).strip("-._")
    return slug or "local-model"


def ollama_model_name(repo_id: str, file: str, meta: dict[str, Any]) -> str:
    base = str(meta.get("general.name") or "").strip()
    if not base:
        base = Path(file).stem if file else repo_id.split("/")[-1]
    base = re.sub(r"\.gguf$", "", base, flags=re.IGNORECASE)
    base = re.sub(
        r"(?i)[-_.]?(iq\d+[a-z0-9_]*|q\d+[a-z0-9_]*|bf16|f16|f32|fp16)$",
        "",
        base,
    )
    if sanitize_model_name(base) in _GENERIC_NAMES:
        base = repo_id.split("/")[-1] or Path(file).stem
    slug = sanitize_model_name(base)
    if slug in _GENERIC_NAMES:
        suffix = hashlib.sha1(f"{repo_id}/{file}".encode("utf-8")).hexdigest()[:6]
        slug = f"{slug}-{suffix}"
    quant = quantization_of(file)
    if quant and not slug.endswith(quant.lower()):
        slug = f"{slug}-{quant.lower()}"
    return slug


def build_catalog_entry(
    *,
    repo_id: str,
    file: str,
    source: str,
    meta: dict[str, Any],
    display_name: str = "",
    context_length: int = 0,
) -> dict[str, Any]:
    from config import get_settings

    settings = get_settings()
    arch = str(meta.get("general.architecture") or "").strip()
    ctx = int(context_length or 0) or _first_int(meta, [
        f"{arch}.context_length",
        "general.context_length",
        f"{arch}.context_size",
        "general.context_size",
    ]) or 32768
    template = str(
        meta.get(f"{arch}.chat_template")
        or meta.get("tokenizer.chat_template")
        or ""
    )
    name = str(meta.get("general.name") or "").strip()
    repo_base = repo_id.split("/")[-1] if repo_id else Path(file).stem
    base_name = name or repo_base
    quant = quantization_of(file)
    label = display_name.strip() or (f"{base_name} ({quant})" if quant else base_name)

    llm_max = int(getattr(settings, "llm_max_tokens", 32000) or 32000)
    max_output = min(llm_max, max(1024, ctx // 4))

    lowered = f"{arch} {base_name} {file}".lower()
    vision = any(token in lowered for token in ("vision", "llava", "mmproj")) or bool(
        re.search(r"(^|[^a-z])vl([^a-z]|$)", lowered)
    )
    reasoning = "think" in template.lower() or "reason" in lowered
    tool_use = arch.lower() not in {"clip", "bert", "whisper", "t5", "vit"}

    params = meta.get("general.parameter_count")
    size_b = float(params) / 1e9 if isinstance(params, (int, float)) and params else parameter_size_of(file) or parameter_size_of(repo_id)

    model_name = ollama_model_name(repo_id, file, meta)
    description = f"本地 GGUF 模型（{source} / {repo_id}）"
    if size_b:
        description += f"，约 {size_b:g}B 参数"
    if quant:
        description += f"，{quant} 量化"

    return {
        "id": f"ollama_chat/{model_name}:latest",
        "provider": "ollama",
        "family": arch or repo_base,
        "name": label,
        "description": description,
        "capabilities": {
            "tool_use": tool_use,
            "vision": vision,
            "reasoning": reasoning,
        },
        "context_length": ctx,
        "limits": {"max_output_tokens": max_output},
        "cost": {
            "input_per_1m": 0.0,
            "output_per_1m": 0.0,
            "cache_read_per_1m": 0.0,
            "cache_write_per_1m": 0.0,
        },
        "local": {
            "source": source,
            "repo_id": repo_id,
            "file": file,
            "quantization": quant,
            "parameter_b": size_b,
            "ollama_model": model_name,
        },
    }
