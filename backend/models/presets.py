from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_lock = threading.RLock()
_cache: list[dict[str, Any]] | None = None
_cache_path: str = ""


def _p(pid, group, label, name, type_, api_base, package="openai", litellm_prefix="", note=""):
    return {
        "id": pid,
        "group": group,
        "label": label,
        "name": name,
        "type": type_,
        "api_base": api_base,
        "package": package,
        "litellm_prefix": litellm_prefix,
        "note": note,
    }


BUILTIN_PRESETS: list[dict[str, Any]] = [
    _p("openai", "intl", "OpenAI", "openai", "openai", "https://api.openai.com/v1"),
    _p("anthropic", "intl", "Anthropic Claude", "anthropic", "openai", "https://api.anthropic.com", litellm_prefix=""),
    _p("google", "intl", "Google Gemini", "google", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    _p("deepseek", "intl", "DeepSeek", "deepseek", "openai", "https://api.deepseek.com", litellm_prefix=""),
    _p("openrouter", "intl", "OpenRouter", "openrouter", "openai", "https://openrouter.ai/api/v1", litellm_prefix="openai"),
    _p("groq", "intl", "Groq", "groq", "openai", "https://api.groq.com/openai/v1", litellm_prefix="openai"),
    _p("mistral", "intl", "Mistral AI", "mistral", "openai", "https://api.mistral.ai/v1", litellm_prefix="openai"),
    _p("together", "intl", "Together AI", "together_ai", "openai", "https://api.together.xyz/v1", litellm_prefix="openai"),
    _p("fireworks", "intl", "Fireworks AI", "fireworks_ai", "openai", "https://api.fireworks.ai/inference/v1", litellm_prefix="openai"),
    _p("xai", "intl", "xAI (Grok)", "xai", "openai", "https://api.x.ai/v1", litellm_prefix="openai"),
    _p("cohere", "intl", "Cohere", "cohere", "openai", "https://api.cohere.com/v2", litellm_prefix="openai"),
    _p("perplexity", "intl", "Perplexity", "perplexity", "openai", "https://api.perplexity.ai", litellm_prefix="openai"),
    _p("cerebras", "intl", "Cerebras", "cerebras", "openai", "https://api.cerebras.ai/v1", litellm_prefix="openai"),
    _p("sambanova", "intl", "SambaNova", "sambanova", "openai", "https://api.sambanova.ai/v1", litellm_prefix="openai"),
    _p("deepinfra", "intl", "DeepInfra", "deepinfra", "openai", "https://api.deepinfra.com/v1/openai", litellm_prefix="openai"),
    _p("novita", "intl", "Novita AI", "novita", "openai", "https://api.novita.ai/v3/openai", litellm_prefix="openai"),
    _p("huggingface", "intl", "Hugging Face", "huggingface", "openai", "https://api-inference.huggingface.co/v1", litellm_prefix="openai"),
    _p("github", "intl", "GitHub Models", "github", "openai", "https://models.inference.ai.azure.com", litellm_prefix="openai"),
    _p("azure", "intl", "Azure OpenAI", "azure", "openai", "", note="api_base 填 https://<resource>.openai.azure.com；api_version 放 compatibility"),
    _p("bedrock", "intl", "AWS Bedrock", "bedrock", "openai", "", note="通过 AWS 环境变量提供凭证；api_base 可留空"),
    _p("vertex", "intl", "Google Vertex AI", "vertex_ai", "openai", "", note="通过 GCP 环境变量提供凭证；api_base 可留空"),
    _p("dashscope", "cn", "阿里云百炼（通义千问）", "dashscope", "openai", "https://dashscope.aliyuncs.com/compatible-mode/v1", litellm_prefix="openai"),
    _p("volcengine", "cn", "火山引擎方舟（豆包）", "volcengine", "openai", "https://ark.cn-beijing.volces.com/api/v3", litellm_prefix="openai"),
    _p("moonshot", "cn", "月之暗面 Kimi", "moonshot", "openai", "https://api.moonshot.cn/v1", litellm_prefix="openai"),
    _p("minimax", "cn", "MiniMax", "minimax", "openai", "https://api.minimax.chat/v1", litellm_prefix="openai"),
    _p("zhipu", "cn", "智谱 GLM", "zhipu", "openai", "https://open.bigmodel.cn/api/paas/v4", litellm_prefix="openai"),
    _p("qianfan", "cn", "百度千帆", "qianfan", "openai", "https://qianfan.baidubce.com/v2", litellm_prefix="openai"),
    _p("hunyuan", "cn", "腾讯混元", "hunyuan", "openai", "https://api.hunyuan.cloud.tencent.com/v1", litellm_prefix="openai"),
    _p("siliconflow", "cn", "硅基流动 SiliconFlow", "siliconflow", "openai", "https://api.siliconflow.cn/v1", litellm_prefix="openai"),
    _p("modelscope", "cn", "魔搭 ModelScope", "modelscope", "openai", "https://api-inference.modelscope.cn/v1", litellm_prefix="openai"),
    _p("stepfun", "cn", "阶跃星辰", "stepfun", "openai", "https://api.stepfun.com/v1", litellm_prefix="openai"),
    _p("baichuan", "cn", "百川智能", "baichuan", "openai", "https://api.baichuan-ai.com/v1", litellm_prefix="openai"),
    _p("yi", "cn", "零一万物", "yi", "openai", "https://api.lingyiwanwu.com/v1", litellm_prefix="openai"),
    _p("ollama-cloud", "local", "Ollama Cloud", "ollama-cloud", "ollama", "https://ollama.com", package="ollama"),
    _p("ollama-local", "local", "Ollama（本地）", "ollama", "ollama", "http://localhost:11434", package="ollama", note="本地 Ollama 通常无需新建 Provider，模型会自动探测"),
    _p("lmstudio", "local", "LM Studio", "lmstudio", "openai", "http://localhost:1234/v1", litellm_prefix="openai"),
    _p("vllm", "local", "vLLM / 本地兼容服务", "vllm", "openai", "http://localhost:8000/v1", litellm_prefix="openai"),
    _p("custom", "custom", "自定义 / OpenAI 兼容", "", "openai", "", litellm_prefix="openai"),
]


def presets_path() -> Path:
    from storage.paths import global_paths

    return global_paths()["data"] / "provider_presets.json"


def _normalize(data: dict[str, Any], pid: str | None = None) -> dict[str, Any]:
    ident = str((data.get("id") or pid or "")).strip().lower()
    if not _ID_RE.fullmatch(ident):
        raise ValueError("字典 ID 只能包含小写字母、数字、下划线和连字符，且以字母或数字开头")
    label = str(data.get("label") or "").strip()
    if not label:
        raise ValueError("请填写显示名称（label）")
    name = str(data.get("name") or "").strip().lower()
    if name and not _ID_RE.fullmatch(name):
        raise ValueError("模型前缀（name）格式非法")
    type_ = str(data.get("type") or "openai").strip().lower()
    if not type_ or not _ID_RE.fullmatch(type_):
        raise ValueError("接口类型格式非法")
    group = str(data.get("group") or "custom").strip().lower()
    if not group or not _ID_RE.fullmatch(group):
        group = "custom"
    litellm_prefix = str(data.get("litellm_prefix") or "").strip().lower()
    if litellm_prefix and not _ID_RE.fullmatch(litellm_prefix):
        raise ValueError("litellm_prefix 格式非法")
    return {
        "id": ident,
        "group": group,
        "label": label,
        "name": name,
        "type": type_,
        "api_base": str(data.get("api_base") or "").strip(),
        "package": str(data.get("package") or ("ollama" if type_ == "ollama" else "openai")).strip(),
        "litellm_prefix": litellm_prefix,
        "note": str(data.get("note") or "").strip(),
    }


def _read_unlocked() -> list[dict[str, Any]]:
    global _cache, _cache_path
    path = presets_path()
    key = str(path)
    if _cache is not None and _cache_path == key:
        return _cache
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        data = {"presets": [dict(p) for p in BUILTIN_PRESETS]}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _cache = data["presets"]
    else:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = raw.get("presets") if isinstance(raw, dict) else raw
            _cache = [dict(p) for p in (items or []) if isinstance(p, dict)]
        except (OSError, ValueError):
            _cache = [dict(p) for p in BUILTIN_PRESETS]
    _cache_path = key
    return _cache


def _write_unlocked(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global _cache
    path = presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"presets": items}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _cache = items
    _cache_path = str(path)
    return items


def list_presets() -> list[dict[str, Any]]:
    with _lock:
        return [dict(p) for p in _read_unlocked()]


def upsert_preset(data: dict[str, Any], pid: str | None = None) -> dict[str, Any]:
    with _lock:
        items = _read_unlocked()
        merged = dict(data)
        if pid:
            for existing in items:
                if str(existing.get("id") or "").lower() == pid.lower():
                    merged = {**existing, **{k: v for k, v in data.items() if v is not None}}
                    break
        entry = _normalize(merged, pid)
        for i, existing in enumerate(items):
            if str(existing.get("id") or "").lower() == entry["id"]:
                items[i] = entry
                break
        else:
            items.append(entry)
        _write_unlocked(items)
        return dict(entry)


def remove_preset(pid: str) -> bool:
    with _lock:
        items = _read_unlocked()
        kept = [p for p in items if str(p.get("id") or "").lower() != pid.lower()]
        if len(kept) == len(items):
            return False
        _write_unlocked(kept)
        return True


def reset_presets() -> list[dict[str, Any]]:
    with _lock:
        return [dict(p) for p in _write_unlocked([dict(p) for p in BUILTIN_PRESETS])]
