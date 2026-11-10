"""Model hub sources: ModelScope (preferred) then HuggingFace fallback.

Only GGUF files are offered (P1 local runtime is Ollama). Split GGUF volumes
(`*-00001-of-00004.gguf`) are grouped into one logical entry so the UI/download
logic deals with a single selectable model.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

_SUPPORTED = ("modelscope", "huggingface")
_SPLIT_RE = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


class SourceError(RuntimeError):
    pass


def _settings():
    from config import get_settings

    return get_settings()


def endpoints() -> dict[str, str]:
    s = _settings()
    return {
        "modelscope": str(getattr(s, "modelscope_endpoint", "") or "https://modelscope.cn").rstrip("/"),
        "huggingface": str(getattr(s, "huggingface_endpoint", "") or "https://huggingface.co").rstrip("/"),
    }


def source_priority() -> list[str]:
    raw = str(getattr(_settings(), "local_model_source_priority", "") or "")
    order = [x.strip().lower() for x in raw.split(",") if x.strip()]
    order = [x for x in order if x in _SUPPORTED]
    return order or ["modelscope", "huggingface"]


def resolve_sources(source: str | None) -> list[str]:
    source = (source or "auto").strip().lower()
    if source in _SUPPORTED:
        return [source]
    return source_priority()


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None,
                  timeout: float = 20.0) -> Any:
    from net.egress import urllib_opener

    data = None
    headers = {"User-Agent": "agentsuper/0.1", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib_opener().open(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _hf_search(query: str, limit: int) -> list[dict[str, Any]]:
    base = endpoints()["huggingface"]
    url = f"{base}/api/models?search={urllib.parse.quote(query)}&limit={limit}"
    data = _request_json(url)
    out: list[dict[str, Any]] = []
    for item in data or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        out.append({
            "id": str(item["id"]),
            "source": "huggingface",
            "name": str(item.get("id")),
            "downloads": int(item.get("downloads") or 0),
            "tags": [str(t) for t in (item.get("tags") or [])][:8],
        })
    return out


def _ms_search(query: str, limit: int) -> list[dict[str, Any]]:
    base = endpoints()["modelscope"]
    payload = {
        "page_number": 1,
        "page_size": max(1, min(limit, 50)),
        "PageNumber": 1,
        "PageSize": max(1, min(limit, 50)),
        "Name": query,
    }
    try:
        data = _request_json(f"{base}/api/v1/models", method="PUT", payload=payload)
        models = ((data or {}).get("Data") or {}).get("Models") or []
    except Exception:
        # Fallback to the newer OpenAPI shape; some deployments only expose one.
        url = f"{base}/openapi/v1/models?search={urllib.parse.quote(query)}&page_size={limit}"
        data = _request_json(url)
        models = ((data or {}).get("data") or {}).get("models") or []
    out: list[dict[str, Any]] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        path = str(m.get("Path") or "").strip()
        name = str(m.get("Name") or m.get("id") or "").split("/")[-1]
        mid = m.get("id") or (f"{path}/{name}" if path and name else "")
        if not mid:
            continue
        out.append({
            "id": str(mid),
            "source": "modelscope",
            "name": str(m.get("ChineseName") or m.get("name") or name or mid),
            "downloads": int(m.get("Downloads") or m.get("downloads") or 0),
            "tags": [str(t) for t in (m.get("Tags") or m.get("tags") or [])][:8],
        })
    return out


def search_models(query: str, source: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []
    errors: list[str] = []
    for src in resolve_sources(source):
        try:
            results = _ms_search(query, limit) if src == "modelscope" else _hf_search(query, limit)
        except Exception as exc:  # noqa: BLE001 - try next source
            errors.append(f"{src}: {exc}")
            continue
        if results:
            return results[:limit]
    if errors:
        raise SourceError("；".join(errors))
    return []


def _group_gguf(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plain: dict[str, int] = {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for f in files:
        name = str(f.get("file") or "").strip()
        if not name.lower().endswith(".gguf"):
            continue
        size = int(f.get("size") or 0)
        m = _SPLIT_RE.match(name)
        if not m:
            plain[name] = size
            continue
        prefix, _idx, total = m.group(1), m.group(2), m.group(3)
        key = (prefix, total)
        group = groups.setdefault(key, {"files": [], "size": 0})
        group["files"].append(name)
        group["size"] += size
    out: list[dict[str, Any]] = []
    for name, size in plain.items():
        out.append({"file": name, "files": [name], "size": size, "split": False})
    for (prefix, total), group in groups.items():
        parts = sorted(group["files"])
        out.append({
            "file": parts[0],
            "files": parts,
            "size": int(group["size"]),
            "split": True,
            "parts": int(total),
        })
    for item in out:
        item["quantization"] = quantization_of(item["file"])
    return sorted(out, key=lambda x: (x["file"].lower()))


def list_gguf_files(repo_id: str, source: str | None = None) -> list[dict[str, Any]]:
    repo_id = (repo_id or "").strip()
    if not repo_id:
        raise SourceError("repo_id is required")
    errors: list[str] = []
    for src in resolve_sources(source):
        try:
            files = _ms_files(src, repo_id) if src == "modelscope" else _hf_files(src, repo_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{src}: {exc}")
            continue
        grouped = _group_gguf(files)
        if grouped:
            for item in grouped:
                item["source"] = src
                item["repo_id"] = repo_id
            return grouped
    if errors:
        raise SourceError("；".join(errors))
    return []


def _hf_files(source: str, repo_id: str) -> list[dict[str, Any]]:
    base = endpoints()[source]
    data = _request_json(f"{base}/api/models/{urllib.parse.quote(repo_id)}?blobs=true")
    siblings = (data or {}).get("siblings") or []
    return [
        {"file": str(s.get("rfilename") or ""), "size": int(s.get("size") or 0)}
        for s in siblings
        if isinstance(s, dict) and s.get("rfilename")
    ]


def _ms_files(source: str, repo_id: str) -> list[dict[str, Any]]:
    base = endpoints()[source]
    quote = urllib.parse.quote(repo_id)
    data = _request_json(f"{base}/api/v1/models/{quote}/repo/files?Revision=master")
    files = ((data or {}).get("Data") or {}).get("Files") or []
    out: list[dict[str, Any]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        name = str(f.get("Path") or f.get("Name") or "")
        if name:
            out.append({"file": name, "size": int(f.get("Size") or 0)})
    return out


def download_url(source: str, repo_id: str, file: str) -> str:
    base = endpoints()[source]
    if source == "huggingface":
        return f"{base}/{urllib.parse.quote(repo_id)}/resolve/main/{urllib.parse.quote(file)}"
    return (
        f"{base}/api/v1/models/{urllib.parse.quote(repo_id)}/repo"
        f"?Revision=master&FilePath={urllib.parse.quote(file)}"
    )


def quantization_of(name: str) -> str:
    m = re.search(
        r"(?i)(IQ\d+(?:_[A-Z0-9]+)*|Q\d+(?:_[A-Z0-9]+)*|BF16|F16|F32|FP16)",
        name,
    )
    return m.group(1).upper() if m else ""


def parameter_size_of(text: str) -> float:
    m = re.search(r"(?i)(\d+(?:\.\d+)?)\s*B(?![A-Za-z])", text or "")
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0
