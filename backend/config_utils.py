import json
import os
from pathlib import Path
from typing import Any, Dict

SENSITIVE_KEYS = {
    "llm_api_key", "db_password", "auth_secret", "admin_token",
    "image_caption_api_key", "tesseract_cmd", "tessdata_dir",
}

ENUM_CHECKS = {
    "external_path_default": {"ask", "allow", "deny"},
    "deepseek_thinking": {"enabled", "disabled", "adaptive"},
    "ollama_thinking": {"enabled", "disabled", "adaptive"},
    "db_type": {"sqlite", "mysql", "postgresql"},
    "voice_tts_model_size": {"0.6B", "1.7B"},
}

def normalize_settings(s: Any) -> Any:
    # normalize paths
    base = Path(__file__).parent
    for k in ["vector_store_path", "plugins_dir", "memory_persist_path", "auth_users_path"]:
        v = getattr(s, k, None)
        if isinstance(v, str) and v:
            p = Path(v)
            if not p.is_absolute():
                p2 = (base / p).resolve()
                try:
                    setattr(s, k, str(p2.relative_to(base)) if p2.is_relative_to(base) else str(p2))
                except Exception:
                    setattr(s, k, str(p2))
    # empty string -> None for optional
    for k in ["llm_api_key", "llm_api_base", "db_url", "admin_token", "auth_secret", "tesseract_cmd", "tessdata_dir", "image_caption_api_key", "image_caption_api_base", "compress_model", "summarization_model"]:
        v = getattr(s, k, None)
        if v == "":
            setattr(s, k, None)
    # lists
    if hasattr(s, "cors_origins") and isinstance(s.cors_origins, list):
        # deduplicate
        seen = []
        for x in s.cors_origins:
            if x not in seen:
                seen.append(x)
        s.cors_origins = seen
    return s


def validate_settings(s: Any) -> list:
    errors = []
    try:
        if getattr(s, "llm_max_tokens", 0) < 1 or getattr(s, "llm_max_tokens", 0) > 200000:
            errors.append("llm_max_tokens out of range")
        if getattr(s, "max_concurrent_agents", 0) < 1:
            errors.append("max_concurrent_agents must be >=1")
        if getattr(s, "chunk_size", 0) < 1:
            errors.append("chunk_size must be >=1")
        if getattr(s, "chunk_overlap", -1) < 0:
            errors.append("chunk_overlap must be >=0")
        if getattr(s, "permission_approval_timeout", 0) < 1:
            errors.append("permission_approval_timeout must be >=1")
        if getattr(s, "ollama_num_ctx", 0) < 1:
            errors.append("ollama_num_ctx must be >=1")
        for k, allowed in ENUM_CHECKS.items():
            v = getattr(s, k, None)
            if v is not None and v not in allowed:
                errors.append(f"{k} must be in {sorted(list(allowed))}")
    except Exception as e:
        errors.append(str(e))
    return errors


def effective_summary(s: Any) -> Dict[str, Any]:
    keys = [
        "llm_model", "llm_api_base", "db_type", "vector_store_path", "plugins_dir",
        "embedding_model", "enable_reranker", "reranker_model",
        "max_concurrent_agents", "cors_origins", "external_path_default",
        "permission_approval_timeout", "allow_source_writes",
        "deepseek_thinking", "ollama_thinking", "ollama_num_ctx",
        "ollama_context_guard", "litellm_debug",
        "compress_model_enabled", "llm_max_tokens", "chain_log_enabled",
        "voice_tts_enabled", "session_budget", "plan_model_enabled", "plan_parallel_enabled",
    ]
    out = {}
    for k in keys:
        v = getattr(s, k, None)
        if k in SENSITIVE_KEYS:
            continue
        out[k] = v
    # mask presence of sensitive
    for k in SENSITIVE_KEYS:
        v = getattr(s, k, None)
        if v is not None and v != "":
            out[f"{k}_set"] = True
        else:
            out[f"{k}_set"] = False
    return out
