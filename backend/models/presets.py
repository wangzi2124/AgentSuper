from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from models import catalog_db

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SEEDED_KEY = "provider_presets_seeded"

_lock = threading.RLock()


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


def storage_path() -> Path:
    return catalog_db.catalog_db_path()


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


def _connect():
    conn = catalog_db._connect()
    try:
        _ensure_seeded(conn)
        return conn
    except Exception:
        conn.close()
        raise


def _ensure_seeded(conn) -> None:
    row = conn.execute(
        "SELECT value FROM provider_presets_meta WHERE key = ?", (_SEEDED_KEY,)
    ).fetchone()
    if row and str(row[0]) == "1":
        return
    count = conn.execute("SELECT COUNT(*) FROM provider_presets").fetchone()[0]
    if count:
        _mark_seeded(conn)
        return

    entries: dict[str, dict[str, Any]] = {p["id"]: dict(p) for p in BUILTIN_PRESETS}
    legacy = presets_path()
    if legacy.exists():
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
            source = raw.get("presets") if isinstance(raw, dict) else raw
            for item in source or []:
                if isinstance(item, dict) and item.get("id"):
                    entries[str(item["id"]).lower()] = dict(item)
        except (OSError, ValueError):
            pass

    seen: set[str] = set()
    for raw_item in entries.values():
        try:
            entry = _normalize(raw_item)
        except ValueError:
            continue
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        conn.execute(
            "INSERT INTO provider_presets"
            " (id, group_name, label, name, api_type, api_base, package, litellm_prefix, note, sort_order)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry["id"], entry["group"], entry["label"], entry["name"], entry["type"],
                entry["api_base"], entry["package"], entry["litellm_prefix"], entry["note"],
                len(seen) - 1,
            ),
        )
    _mark_seeded(conn)


def _mark_seeded(conn) -> None:
    conn.execute("DELETE FROM provider_presets_meta WHERE key = ?", (_SEEDED_KEY,))
    conn.execute(
        "INSERT INTO provider_presets_meta(key, value) VALUES (?, ?)", (_SEEDED_KEY, "1")
    )
    conn.commit()


def _row_to_preset(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "group": row["group_name"],
        "label": row["label"],
        "name": row["name"],
        "type": row["api_type"],
        "api_base": row["api_base"],
        "package": row["package"],
        "litellm_prefix": row["litellm_prefix"],
        "note": row["note"],
    }


def list_presets() -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, group_name, label, name, api_type, api_base, package,"
                " litellm_prefix, note FROM provider_presets ORDER BY sort_order, id"
            ).fetchall()
        finally:
            conn.close()
    return [_row_to_preset(row) for row in rows]


def upsert_preset(data: dict[str, Any], pid: str | None = None) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            lookup = str(pid or data.get("id") or "").strip().lower()
            existing = None
            if lookup:
                existing = conn.execute(
                    "SELECT id, group_name, label, name, api_type, api_base, package,"
                    " litellm_prefix, note, sort_order FROM provider_presets WHERE id = ?",
                    (lookup,),
                ).fetchone()
            merged = dict(data)
            if pid and existing is not None:
                merged = {
                    **_row_to_preset(existing),
                    **{k: v for k, v in data.items() if v is not None},
                }
            entry = _normalize(merged, pid)
            current = conn.execute(
                "SELECT sort_order FROM provider_presets WHERE id = ?", (entry["id"],)
            ).fetchone()
            values = (
                entry["group"], entry["label"], entry["name"], entry["type"],
                entry["api_base"], entry["package"], entry["litellm_prefix"], entry["note"],
            )
            if current is not None:
                conn.execute(
                    "UPDATE provider_presets SET group_name = ?, label = ?, name = ?,"
                    " api_type = ?, api_base = ?, package = ?, litellm_prefix = ?, note = ?"
                    " WHERE id = ?",
                    (*values, entry["id"]),
                )
            else:
                next_order = conn.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM provider_presets"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO provider_presets"
                    " (id, group_name, label, name, api_type, api_base, package, litellm_prefix, note, sort_order)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry["id"], *values, next_order),
                )
            conn.commit()
        finally:
            conn.close()
    return dict(entry)


def remove_preset(pid: str) -> bool:
    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                "DELETE FROM provider_presets WHERE id = ?", (pid.strip().lower(),)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()
