"""Import downloaded GGUF files into the local Ollama runtime and register them."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from app.local_models import metadata as meta_mod

_CHUNK = 1024 * 1024


class ImportError_(RuntimeError):
    pass


def ollama_base_url() -> str:
    from models.catalog import _ollama_base_url

    return _ollama_base_url()


def _request(url: str, data: Any, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    from net.egress import urllib_opener

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib_opener().open(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return int(exc.code), body


def file_digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return f"{algorithm}:{digest.hexdigest()}"


def _iter_file(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            yield chunk


def upload_blob(path: Path) -> str:
    """Upload a GGUF to Ollama's blob store; returns ``sha256:<hex>``."""
    digest = file_digest(path)
    status, body = _request(
        f"{ollama_base_url()}/api/blobs/{digest}",
        _iter_file(path),
        {"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)},
        timeout=1800,
    )
    if status >= 400:
        raise ImportError_(f"Ollama 上传文件失败（{path.name}）：HTTP {status} {body[:300]}")
    return digest


def _legacy_modelfile(paths: list[Path], num_ctx: int) -> str:
    lines = [f'FROM "{p.as_posix()}"' for p in paths]
    if num_ctx:
        lines.append(f"PARAMETER num_ctx {int(num_ctx)}")
    return "\n".join(lines)


def create_ollama_model(name: str, paths: list[Path], num_ctx: int = 0) -> None:
    for path in paths:
        if not path.exists():
            raise ImportError_(f"GGUF 文件不存在：{path}")
    base = ollama_base_url()
    params: dict[str, Any] = {}
    if num_ctx:
        params["num_ctx"] = int(num_ctx)

    errors: list[str] = []
    try:
        files = {p.name: upload_blob(p) for p in paths}
        status, body = _request(
            f"{base}/api/create",
            json.dumps({"model": name, "files": files, "parameters": params, "stream": False}).encode(),
            {"Content-Type": "application/json"},
            timeout=1800,
        )
        if status < 400:
            _ensure_success(body)
            return
        errors.append(f"files API HTTP {status}: {body[:300]}")
    except ImportError_ as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    # Older Ollama (<0.40) only accepts a Modelfile payload.
    modelfile = _legacy_modelfile(paths, num_ctx)
    try:
        status, body = _request(
            f"{base}/api/create",
            json.dumps({"name": name, "model": name, "modelfile": modelfile, "stream": False}).encode(),
            {"Content-Type": "application/json"},
            timeout=1800,
        )
        if status < 400:
            _ensure_success(body)
            return
        errors.append(f"modelfile API HTTP {status}: {body[:300]}")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    raise ImportError_("Ollama 导入失败：" + "；".join(errors[-2:]))


def delete_ollama_model(name: str) -> None:
    """Remove a model from Ollama; missing models are ignored."""
    if not name:
        return
    status, body = _request(
        f"{ollama_base_url()}/api/delete",
        json.dumps({"model": name}).encode(),
        {"Content-Type": "application/json"},
        timeout=120,
    )
    if status >= 400 and "not found" not in body.lower():
        raise ImportError_(f"Ollama 删除模型失败（{name}）：HTTP {status} {body[:300]}")


def _ensure_success(body: str) -> None:
    text = (body or "").strip()
    if not text:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("error"):
                raise ImportError_(f"Ollama 导入失败：{payload['error']}")
            if str(payload.get("status") or "").lower() == "success":
                return
        return
    if payload.get("error"):
        raise ImportError_(f"Ollama 导入失败：{payload['error']}")
    status = str(payload.get("status") or "").lower()
    if status and status != "success":
        raise ImportError_(f"Ollama 导入未成功：{status}")


def import_downloaded(
    *,
    job_id: str,
    repo_id: str,
    file: str,
    source: str,
    gguf_paths: list[str | Path],
    display_name: str = "",
    context_length: int = 0,
) -> dict[str, Any]:
    from models import catalog

    paths = [Path(p) for p in gguf_paths]
    if not paths:
        raise ImportError_("没有可导入的 GGUF 文件")
    meta = meta_mod.parse_gguf_metadata(paths[0])
    entry = meta_mod.build_catalog_entry(
        repo_id=repo_id,
        file=file,
        source=source,
        meta=meta,
        display_name=display_name,
        context_length=context_length,
    )
    local = entry.setdefault("local", {})
    local["job_id"] = job_id
    local["path"] = str(paths[0])
    local["files"] = [str(p) for p in paths]
    name = str(local.get("ollama_model") or entry["id"].split("/", 1)[-1].split(":")[0])
    create_ollama_model(name, paths, int(entry.get("context_length") or 0))
    catalog.upsert_custom_model(entry)
    return entry
