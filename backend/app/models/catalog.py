"""模型目录（model catalog）：单一事实来源，provider/model → 能力/上下文/限长/价格。

设计对齐 opencode v2（config.ts:36-38 顶层 model 为 "provider/model" 字符串、
provider.ts ConfigProvider.Info.models 汇总可用模型，价格走各 provider usage 换算）。

- 内置目录：覆盖前端 SUPPORTED_MODELS 全部条目（价格未知记 0，可覆盖）。
- 用户覆盖：`data/model_catalog.db`（SQLite，见 catalog_db.py）——
  `{"overrides": {<id>: {fields…}}, "extra": [{完整条目…}], "default_model": "…"}` 同构数据。
  overrides 按 id 深合并（可改价格/能力/上下文，或新增本地/私有模型）；
  旧 `data/model_catalog.json` 在首次启动自动导入后仅作迁移备份，不再写入。
- 计价：`resolve_cost()` 按目录单价换算（每 1M tokens USD）：非缓存输入按输入价、
  cache_read 按命中价、cache_write 按写入价、输出按输出价；无价格单位记 0。

注意口径：usage 的 `input`（会话存储）沿用 litellm 语义 = 总输入（含缓存）；
"非缓存输入" = input - cache_read，仅在成本/展示口径推导，不改动已入库字段语义。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

# ── 内置目录 ─────────────────────────────────────────────────────────────
# cost 单位：USD / 每 1M tokens。价格未知记 0（opencode 当前即硬编码 0 的成本态）。
# deepseek v4 系列暂无官方价，以下用 DeepSeek 家族参考价（deepseek-chat V3 口径），
# 可在 data/model_catalog.json 里按实际合同价覆盖。
_BUILTIN_CATALOG: list[dict[str, Any]] = [
    {
        "id": "deepseek/deepseek-v4-flash",
        "provider": "deepseek",
        "family": "deepseek-v4",
        "name": "DeepSeek V4 Flash",
        "description": "轻量高速模型，日常问答与多智能体调度首选，速度与质量兼顾",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": True},
        "context_length": 160000,
        "limits": {"max_output_tokens": 8192},
        "cost": {"input_per_1m": 0.27, "output_per_1m": 1.10,
                 "cache_read_per_1m": 0.07, "cache_write_per_1m": 0.0},
        "default": True,
    },
    {
        "id": "deepseek/deepseek-v4-pro",
        "provider": "deepseek",
        "family": "deepseek-v4",
        "name": "DeepSeek V4 Pro",
        "description": "旗舰推理模型，复杂任务、长文本与深度分析能力更强",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": True},
        "context_length": 160000,
        "limits": {"max_output_tokens": 8192},
        "cost": {"input_per_1m": 0.27, "output_per_1m": 1.10,
                 "cache_read_per_1m": 0.07, "cache_write_per_1m": 0.0},
    },
    {
        "id": "openai/gpt-4o",
        "provider": "openai",
        "family": "gpt-4o",
        "name": "OpenAI GPT-4o",
        "description": "OpenAI 多模态旗舰模型，图文理解与通用任务表现均衡",
        "capabilities": {"tool_use": True, "vision": True, "reasoning": False},
        "context_length": 128000,
        "limits": {"max_output_tokens": 16384},
        "cost": {"input_per_1m": 2.50, "output_per_1m": 10.00,
                 "cache_read_per_1m": 1.25, "cache_write_per_1m": 0.0},
    },
    {
        "id": "openai/gpt-4o-mini",
        "provider": "openai",
        "family": "gpt-4o",
        "name": "OpenAI GPT-4o-mini",
        "description": "GPT-4o 轻量版，成本更低、响应更快，适合高频简单任务",
        "capabilities": {"tool_use": True, "vision": True, "reasoning": False},
        "context_length": 128000,
        "limits": {"max_output_tokens": 16384},
        "cost": {"input_per_1m": 0.15, "output_per_1m": 0.60,
                 "cache_read_per_1m": 0.075, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/qwen2.5-coder:latest",
        "provider": "ollama",
        "family": "qwen2.5-coder",
        "name": "Ollama Qwen2.5-Coder",
        "description": "本地代码生成模型，编程与代码任务首选",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 32768,
        "limits": {"max_output_tokens": 8192},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/qwen2.5:7b",
        "provider": "ollama",
        "family": "qwen2.5",
        "name": "Ollama Qwen2.5 7B",
        "description": "通义千问轻量版，中文理解与生成能力均衡",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 32768,
        "limits": {"max_output_tokens": 8192},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/qwen2.5:14b",
        "provider": "ollama",
        "family": "qwen2.5",
        "name": "Ollama Qwen2.5 14B",
        "description": "通义千问中型版，复杂推理与长文本处理能力更强",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 32768,
        "limits": {"max_output_tokens": 8192},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/qwen2.5:32b",
        "provider": "ollama",
        "family": "qwen2.5",
        "name": "Ollama Qwen2.5 32B",
        "description": "通义千问大型版，高质量生成与深度分析",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 32768,
        "limits": {"max_output_tokens": 8192},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/codellama:latest",
        "provider": "ollama",
        "family": "codellama",
        "name": "Ollama CodeLlama",
        "description": "Meta 代码专用模型，Python/JavaScript 编程优化",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 16384,
        "limits": {"max_output_tokens": 4096},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/llama3.1:latest",
        "provider": "ollama",
        "family": "llama3.1",
        "name": "Ollama Llama 3.1",
        "description": "Meta 最新通用模型，多语言支持与推理能力",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 131072,
        "limits": {"max_output_tokens": 4096},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
    {
        "id": "ollama/mistral:latest",
        "provider": "ollama",
        "family": "mistral",
        "name": "Ollama Mistral",
        "description": "轻量高效模型，快速响应与低资源占用",
        "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
        "context_length": 32768,
        "limits": {"max_output_tokens": 4096},
        "cost": {"input_per_1m": 0.0, "output_per_1m": 0.0,
                 "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0},
    },
]

_ZERO_COST = {
    "input_per_1m": 0.0, "output_per_1m": 0.0,
    "cache_read_per_1m": 0.0, "cache_write_per_1m": 0.0,
}


def catalog_path() -> Path:
    """旧 model_catalog.json 覆盖文件（迁移备份，DB 生效后不再写入）。"""
    from app.models.catalog_db import catalog_json_path
    return catalog_json_path()


def catalog_db_path() -> Path:
    """模型目录数据库路径（当前单一事实来源）。"""
    from app.models.catalog_db import catalog_db_path as _dbp
    return _dbp()


def load_config() -> dict[str, Any]:
    """读取模型配置（DB 为单一事实来源；语义与原 JSON 一致）。"""
    from app.models.catalog_db import load_config as _load
    return _load()


def save_config(cfg: dict[str, Any]) -> None:
    """整体写回模型配置到数据库。"""
    from app.models.catalog_db import save_config as _save
    _save(cfg)


def _deep_merge(base: dict, override: dict) -> dict:
    """两层深合并（value 为 dict 时递归）。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def build_catalog(cfg: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """内置目录 + 数据库覆盖（overrides 深合并 / extra 追加）。

    cfg 可注入内存配置（未落库前的自愈校验用），默认自 DB 读取。
    """
    entries = [_deep_merge(dict(e), {}) for e in _BUILTIN_CATALOG]
    by_id: dict[str, dict] = {e["id"]: e for e in entries}
    data = cfg if cfg is not None else load_config()
    for mid, patch in (data.get("overrides") or {}).items():
        if mid not in by_id:
            base = {"id": mid, "provider": mid.split("/", 1)[0],
                    "family": mid, "name": mid, "description": "",
                    "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
                    "cost": dict(_ZERO_COST)}
            by_id[mid] = base
        by_id[mid] = _deep_merge(by_id[mid], dict(patch))
    for extra in data.get("extra") or []:
        if extra.get("id") and extra["id"] not in by_id:
            extra.setdefault("cost", dict(_ZERO_COST))
            extra.setdefault("capabilities", {"tool_use": True, "vision": False, "reasoning": False})
            by_id[extra["id"]] = extra
    return list(by_id.values())


_catalog_cache: dict[str, list[dict[str, Any]]] = {}
_catalog_lock = threading.Lock()


def _cache_key() -> str:
    """按数据目录键隔离缓存：AGENTSUPER_DATA 切换（测试/多实例）不会串陈旧目录。"""
    from app.models.catalog_db import _data_dir
    return str(_data_dir())

# ── Ollama 本地模型探测 ───────────────────────────────────────────────────
# 目录是「声明式」的（内置 + 覆盖文件），但真实环境里 ollama 装了哪些模型是运行时事实。
# 以 60s TTL 探测 ollama /api/tags，把「本机已装但目录未收录」的模型合并进目录快照，
# 这样前端下拉框与默认模型（.env LLM_MODEL，如 ollama/qwen2.5:3b）都能对上真环境。
_OLLAMA_TTL = 60.0
_ollama_cache: Optional[list[dict[str, Any]]] = None
_ollama_cache_time: float = 0.0
_ollama_lock = threading.Lock()


def _ollama_base_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def probe_ollama_models(known_ids: set[str], force: bool = False) -> list[dict[str, Any]]:
    """探测本机 ollama 已安装模型，返回「目录未收录」的增量条目。

    ollama 未启动/不可达/超时一律返回 []（不抛异常，不阻塞启动）；
    成功探测结果按 TTL 缓存，避免每个请求都去打 /api/tags。
    """
    global _ollama_cache, _ollama_cache_time
    now = time.time()
    if not force and _ollama_cache is not None and now - _ollama_cache_time < _OLLAMA_TTL:
        return _ollama_cache
    with _ollama_lock:
        if not force and _ollama_cache is not None and now - _ollama_cache_time < _OLLAMA_TTL:
            return _ollama_cache
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{_ollama_base_url()}/api/tags",
                headers={"User-Agent": "agentsuper/0.1"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            extras: list[dict[str, Any]] = []
            for m in (data.get("models") or []):
                name = (m.get("name") or "").strip()
                if not name:
                    continue
                mid = f"ollama/{name}"
                if mid in known_ids:
                    continue
                family = name.split(":", 1)[0]
                extras.append({
                    "id": mid,
                    "provider": "ollama",
                    "family": family,
                    "name": f"Ollama {family}",
                    "description": "本地 Ollama 模型（探测自 /api/tags）",
                    "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
                    "context_length": 32768,
                    "limits": {"max_output_tokens": 8192},
                    "cost": dict(_ZERO_COST),
                })
            _ollama_cache, _ollama_cache_time = extras, now
            return extras
        except Exception:
            _ollama_cache, _ollama_cache_time = [], now
            return []


def get_catalog(force_reload: bool = False) -> list[dict[str, Any]]:
    """目录快照（按数据目录键的线程安全缓存；force_reload=True 重新读库 + 重探测）。"""
    key = _cache_key()
    cached = _catalog_cache.get(key)
    if cached is None or force_reload:
        with _catalog_lock:
            cached = _catalog_cache.get(key)
            if cached is None or force_reload:
                static = build_catalog()
                known = {e["id"] for e in static}
                probe = probe_ollama_models(known, force=force_reload)
                known |= {e["id"] for e in probe}
                probe = probe + probe_configured_providers(known, force=force_reload)
                cached = static + probe
                _catalog_cache[key] = cached
    return cached


def reload_catalog() -> None:
    get_catalog(force_reload=True)
    return None


# ── Provider 注册表（前端可配置的私有/自建模型服务）────────────────────────
# model_catalog.json 新增 `providers` 键：
#   "providers": {
#     "vllm": {"label": "Local vLLM", "api_base": "http://127.0.0.1:8001/v1",
#              "api_key": "", "enabled": true}
#   }
# 启动探测（probe）会把各 enabled provider 的 OpenAI 兼容 /v1/models 里
# `目录未收录` 的模型自动注册为 "{provider}/{id}" 条目（费用 0，可前端覆盖）。
# `_llm_call` 经 provider_api() 按模型解析 api_base/api_key——即“前端配置模型，
# 不再只依赖后端 .env”。


def read_providers() -> dict[str, dict[str, Any]]:
    """返回 providers 注册表（键 = provider 名）。"""
    cfg = load_config()
    return {k: dict(v) for k, v in (cfg.get("providers") or {}).items()}


def upsert_provider(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """新增/更新一个 provider（前端模型管理写回 model_catalog.db）。"""
    cfg = load_config()
    providers = dict(cfg.get("providers") or {})
    entry = dict(providers.get(name) or {})
    for k in ("label", "api_base", "api_key", "enabled"):
        if k in data and data[k] is not None:
            entry[k] = data[k]
    entry.setdefault("label", name)
    entry.setdefault("api_key", "")
    entry.setdefault("enabled", True)
    entry.setdefault("api_base", "")
    providers[name] = entry
    cfg["providers"] = providers
    save_config(cfg)
    reload_catalog()
    return dict(entry)


def remove_provider(name: str) -> bool:
    cfg = load_config()
    providers = dict(cfg.get("providers") or {})
    if name not in providers:
        return False
    del providers[name]
    cfg["providers"] = providers
    _clear_invalid_defaults(cfg)
    save_config(cfg)
    reload_catalog()
    return True


def upsert_custom_model(entry: dict[str, Any]) -> dict[str, Any]:
    """新增/更新自定义模型。内置目录里的 id → 写 overrides 深合并；否则 extra 追加。"""
    mid = str(entry.get("id") or "").strip()
    if not mid:
        raise ValueError("model id is required")
    cfg = load_config()
    by_id = {e["id"]: e for e in build_catalog()}
    if mid in by_id:
        overrides = dict(cfg.get("overrides") or {})
        overrides[mid] = _deep_merge(overrides.get(mid, {}), _strip_unknown_fields(dict(entry)))
        cfg["overrides"] = overrides
        # 同一 id 若此前存于 extra（探测模型首次添加进 extra、二次编辑升级为 override）
        # 必须从 extra 移除，否则写库时 id 在 overrides 与 extra 各一份 → UNIQUE(id) 冲突 500
        cfg["extra"] = [e for e in (cfg.get("extra") or []) if (e.get("id") if isinstance(e, dict) else None) != mid]
    else:
        entry.setdefault("cost", dict(_ZERO_COST))
        entry.setdefault("capabilities", {"tool_use": True, "vision": False, "reasoning": False})
        entry.setdefault("context_length", 32768)
        entry.setdefault("limits", {"max_output_tokens": 8192})
        extra = [e for e in (cfg.get("extra") or []) if (e.get("id") if isinstance(e, dict) else None) != mid]
        extra.append(entry)
        cfg["extra"] = extra
    save_config(cfg)
    reload_catalog()
    return dict(entry)


def remove_custom_model(mid: str) -> bool:
    """删除自定义模型（overrides 或 extra 中的同名条目）。"""
    cfg = load_config()
    changed = False
    overrides = dict(cfg.get("overrides") or {})
    if mid in overrides:
        del overrides[mid]
        cfg["overrides"] = overrides
        changed = True
    extra = [e for e in (cfg.get("extra") or []) if e.get("id") != mid]
    if len(extra) != len(cfg.get("extra") or []):
        cfg["extra"] = extra
        changed = True
    if changed:
        _clear_invalid_defaults(cfg)
        save_config(cfg)
        reload_catalog()
    return changed


def _clear_invalid_defaults(cfg: dict[str, Any]) -> None:
    """删除模型/Provider 后清掉指向已不存在条目的默认位（DB 自愈）。

    只按声明式目录（builtin + overrides + extra）判断；探测类（ollama/自定义 provider
    运行时注册）的 id 不在其列，由 getter 的运行时守卫兜底，避免误清。
    """
    ids = {e["id"] for e in build_catalog(cfg)}
    for key in ("default_model", "small_model", "image_caption_model"):
        v = cfg.get(key)
        if v and str(v) not in ids:
            cfg[key] = None


def set_defaults(default_model: Optional[str] = None, small_model: Optional[str] = None,
                 image_caption_model: Optional[str] = None, voice_model_size: Optional[str] = None) -> dict[str, Any]:
    """设置默认模型 / 轻量模型 / 图片解析模型 / 语音模型规格（写回 model_catalog.db）。"""
    cfg = load_config()
    if default_model is not None:
        cfg["default_model"] = default_model or None
    if small_model is not None:
        cfg["small_model"] = small_model or None
    if image_caption_model is not None:
        cfg["image_caption_model"] = image_caption_model or None
    if voice_model_size is not None:
        cfg["voice_model_size"] = voice_model_size or None
    save_config(cfg)
    reload_catalog()
    return {"default_model": cfg.get("default_model") or None,
            "small_model": cfg.get("small_model") or None,
            "image_caption_model": cfg.get("image_caption_model") or None,
            "voice_model_size": cfg.get("voice_model_size") or None}


def _strip_unknown_fields(entry: dict) -> dict:
    """只保留目录条目已知字段，避免把前端 UI 噪音字段写进 overrides。"""
    known = {"id", "provider", "family", "name", "description", "capabilities",
             "context_length", "limits", "cost", "default", "tokenizer"}
    return {k: v for k, v in entry.items() if k in known and v is not None}


# ── 自定义 Provider 模型探测（启动自动注册）────────────────────────────────
_PROBE_TTL = 60.0
_probe_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ollama_probe_state: tuple[Optional[list[dict[str, Any]]], float] = (None, 0.0)


def probe_provider_models(provider_name: str, api_base: str, api_key: str,
                          known_ids: set[str], force: bool = False) -> list[dict[str, Any]]:
    """探测 OpenAI 兼容 provider（vLLM/LM Studio/llama.cpp 等）的 /v1/models。

    成功结果按 TTL 缓存；失败/超时返回 []（不阻塞）。
    """
    now = time.time()
    cached = _probe_cache.get(provider_name)
    if cached and not force and now - cached[0] < _PROBE_TTL:
        return cached[1]
    try:
        import urllib.request
        base = str(api_base or "").rstrip("/")
        url = base + "/v1/models" if base else ""
        if not url:
            _probe_cache[provider_name] = (now, [])
            return []
        req = urllib.request.Request(url, headers={"User-Agent": "agentsuper/0.1",
                                                   **(dict({"Authorization": f"Bearer {api_key}"}) if api_key else {})})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        extras: list[dict[str, Any]] = []
        for m in data.get("data") or []:
            mid = m.get("id") or ""
            if not mid:
                continue
            full = f"{provider_name}/{mid}"
            if full in known_ids:
                continue
            title = m.get("owned_by") or provider_name
            extras.append({
                "id": full, "provider": provider_name, "family": provider_name,
                "name": f"{title} {mid}", "description": f"探测自 {provider_name} /v1/models",
                "capabilities": {"tool_use": True, "vision": False, "reasoning": False},
                "context_length": 32768, "limits": {"max_output_tokens": 8192},
                "cost": dict(_ZERO_COST),
            })
        _probe_cache[provider_name] = (now, extras)
        return extras
    except Exception:
        _probe_cache[provider_name] = (now, [])
        return []


def probe_configured_providers(known_ids: set[str], force: bool = False) -> list[dict[str, Any]]:
    """对所有 enabled 的自定义 provider 做 /v1/models 探测，合并增量条目。"""
    out: list[dict[str, Any]] = []
    for name, p in read_providers().items():
        if not p.get("enabled", True) or not p.get("api_base"):
            continue
        try:
            out.extend(probe_provider_models(name, p.get("api_base"), p.get("api_key") or "", known_ids, force=force))
        except Exception:
            continue
    return out


def provider_api(model_id: Optional[str]) -> dict[str, Any]:
    """解析模型对应的调用凭证：{api_base, api_key, is_ollama}。优先级：

    1) model 的 provider 命中注册表（providers.<name>.api_base/api_key）；
    2) ollama 前缀 → OLLAMA_HOST/api_key="ollama"（沿用 litellm 自动发现）；
    3) 未注册的自定义 provider → 回落 settings.llm_api_base/llm_api_key。
    """
    from app.config import settings
    mid = normalize_model(model_id)
    if not mid:
        return {"api_base": settings.llm_api_base, "api_key": settings.llm_api_key, "is_ollama": False}
    provider = mid.split("/", 1)[0] if "/" in mid else ""
    if provider == "ollama":
        return {"api_base": None, "api_key": "ollama", "is_ollama": True}
    reg = read_providers().get(provider)
    if reg:
        return {"api_base": reg.get("api_base") or settings.llm_api_base,
                "api_key": reg.get("api_key") or settings.llm_api_key,
                "is_ollama": False}
    return {"api_base": settings.llm_api_base, "api_key": settings.llm_api_key, "is_ollama": False}


def provider_config_hint(model_id: Optional[str], *, creds: Optional[dict] = None) -> str:
    """检测模型对应的 Provider 是否已配置；未配置则返回友好中文提示，已配置返回 ""。

    判定口径（与 provider_api 的解析优先级一致）：
      - 无模型 / ollama 前缀 / provider 命中注册表且带 api_base → 视为已配置；
      - 否则走 settings.llm_api_base/api_key 兜底：api_key 为空 AND
        （api_base 为空 或 api_base 是 ollama 本地（localhost/127.0.0.1:11434）或默认
        deepseek 端点）→ 说明用户根本没配服务商，返回提示而非把它当自定义 provider 直连。
    提示文案面向「模型管理」页配置 Provider（api_base + api_key），也提及 .env 兜底。
    """
    from app.config import settings

    mid = normalize_model(model_id)
    if not mid:
        return ""
    provider = mid.split("/", 1)[0] if "/" in mid else ""
    if not provider or provider == "ollama":
        return ""
    reg = read_providers().get(provider)
    if reg and reg.get("api_base"):
        return ""
    resolved = creds or provider_api(mid)
    if resolved.get("is_ollama"):
        return ""
    key = (resolved.get("api_key") or "").strip()
    base = (resolved.get("api_base") or "").strip()
    if key:
        return ""
    _local = ("localhost" in base) or base.startswith("127.0.0.1") or base.endswith(":11434")
    _default_ds = base.rstrip("/") == "https://api.deepseek.com"
    if not base or _local or _default_ds:
        return (
            "未配置模型服务商（Provider）「%s」：当前模型的 API 凭证缺失或指向了本地 Ollama/"
            "默认地址，无法发起调用。请在「模型管理」页为该 Provider 配置 api_base 与 api_key"
            "（也可在模型管理直接测试连通性），或在 .env 中设置 LLM_API_BASE/LLM_API_KEY 后重启后端。"
        ) % provider
    return ""


def provider_models_source() -> list[dict[str, Any]]:
    """当前可用的提供商信息（前端模型管理面板）。"""
    out = []
    for name, p in read_providers().items():
        out.append({"provider": name, "label": p.get("label") or name,
                    "api_base": p.get("api_base") or "", "api_key": p.get("api_key") or "",
                    "enabled": bool(p.get("enabled", True)),
                    "models": lookup_all(f"{name}/")})
    return out


def lookup_all(prefix: str = "") -> list[dict[str, Any]]:
    """按 id 前缀筛选目录条目（prefix 形如 "provider/"）。"""
    return [e for e in get_catalog() if e["id"].startswith(prefix)]


def lookup(model_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not model_id:
        return None
    for e in get_catalog():
        if e["id"] == model_id:
            return e
    return None


# ── 模型引用归一化 ───────────────────────────────────────────────────────
def normalize_model(ref: Any, default: Optional[str] = None) -> Optional[str]:
    """把 model 引用（str "provider/model" / 裸名 / dict {id,provider,name}）归一为 id 字符串。

    裸名（无 '/'）不猜 provider：与 graphmod 的 api_base 自动前缀逻辑解耦，
    保留原值交由 _llm_call 处理；目录未收录的模型照样可用（仅缺能力/价格信息）。
    """
    if ref is None or ref == "":
        return default
    if isinstance(ref, str):
        return ref.strip() or default
    if isinstance(ref, dict):
        if ref.get("id"):
            return str(ref["id"])
        p = ref.get("provider", "")
        n = ref.get("name", "")
        if p and n:
            return f"{p}/{n}"
    return default


def model_ref_dict(model_id: Optional[str]) -> Optional[dict[str, Any]]:
    """转换 session 落库用的 ModelRef（{id, provider, name}）。"""
    mid = normalize_model(model_id)
    if not mid:
        return None
    entry = lookup(mid)
    return {
        "id": mid,
        "provider": entry["provider"] if entry else (mid.split("/", 1)[0] if "/" in mid else ""),
        "name": entry["name"] if entry else mid,
    }


def default_model() -> str:
    """默认模型：数据库(默认为模型管理面板配置)优先，其次 settings.llm_model，最后目录首个。"""
    from app.config import settings
    dm = load_config().get("default_model")
    if dm and _id_in_catalog(str(dm)):
        return str(dm)
    if settings.llm_model and _id_in_catalog(str(settings.llm_model)):
        return str(settings.llm_model)
    for e in get_catalog():
        if e.get("default"):
            return e["id"]
    return get_catalog()[0]["id"] if get_catalog() else ""


def _id_in_catalog(mid: str) -> bool:
    return any(e["id"] == mid for e in get_catalog())


def small_model() -> Optional[str]:
    """轻量模型：model_catalog.json 的 small_model（前端可配置）；未设置回退默认模型。"""
    cfg = load_config()
    sm = cfg.get("small_model")
    if sm and _id_in_catalog(str(sm)):
        return str(sm)
    return default_model()


def image_caption_model() -> Optional[str]:
    """图片解析模型：model_catalog.json 的 image_caption_model（模型管理可配）；未配置返回 None。"""
    cfg = load_config()
    v = cfg.get("image_caption_model")
    if v and _id_in_catalog(str(v)):
        return str(v)
    return None


def voice_model_size() -> Optional[str]:
    """语音合成模型规格：model_catalog.json 的 voice_model_size（模型管理可配）；未配置返回 None。"""
    cfg = load_config()
    v = cfg.get("voice_model_size")
    return str(v) if v else None


# ── usage 归一化 / 成本 ──────────────────────────────────────────────────
def _cost(entry: Optional[dict], key: str) -> float:
    if not entry:
        return 0.0
    return float((entry.get("cost") or {}).get(key, 0.0) or 0.0)


def resolve_cost(model_id: Optional[str], *, input_tokens: int = 0, output_tokens: int = 0,
                 cache_read: int = 0, cache_write: int = 0) -> float:
    """按目录单价计算一次 LLM 调用的成本（USD）。

    非缓存输入 = input_tokens - cache_read（对齐 opencode 会话面向口径）。
    """
    entry = lookup(model_id)
    noncached = max(0, int(input_tokens) - int(cache_read))
    return (
        noncached * _cost(entry, "input_per_1m")
        + int(output_tokens) * _cost(entry, "output_per_1m")
        + int(cache_read) * _cost(entry, "cache_read_per_1m")
        + int(cache_write) * _cost(entry, "cache_write_per_1m")
    ) / 1_000_000


def extract_usage(usage: Optional[Any], pt: int = 0, ct: int = 0) -> dict[str, int]:
    """把 provider/litellm usage 归一为五键字典（口径一致，含 reasoning 提取）。

    返回 {input, output, reasoning, cache_read, cache_write}，与
    graphmod.state._ZERO_USAGE 对齐；input 为总输入（含缓存）语义。
    """
    if usage is None:
        return {"input": int(pt or 0), "output": int(ct or 0), "reasoning": 0,
                "cache_read": 0, "cache_write": 0}
    pt = int(pt or getattr(usage, "prompt_tokens", 0) or 0)
    ct = int(ct or getattr(usage, "completion_tokens", 0) or 0)
    hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
    miss = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
    if not hit and not miss:
        det = getattr(usage, "prompt_tokens_details", None)
        if det is not None:
            hit = int(getattr(det, "cached_tokens", 0) or 0)
    if miss == 0 and pt > hit:
        miss = pt - hit
    reasoning = 0
    cdet = getattr(usage, "completion_tokens_details", None)
    if cdet is not None:
        reasoning = int(getattr(cdet, "reasoning_tokens", 0) or 0)
    if not reasoning:
        reasoning = int(getattr(usage, "reasoning_tokens", 0) or 0)
    return {"input": pt, "output": ct, "reasoning": reasoning,
            "cache_read": hit, "cache_write": miss}


def sum_usage(acc: dict[str, int], other: dict[str, int]) -> dict[str, int]:
    """逐键求和两个五键 usage 字典（supervisor 汇总用）。"""
    out = dict(acc)
    for k in ("input", "output", "reasoning", "cache_read", "cache_write"):
        out[k] = int(out.get(k, 0)) + int(other.get(k, 0))
    return out


def cost_of_usage(model_id: Optional[str], tokens: dict[str, int]) -> float:
    return resolve_cost(model_id,
                        input_tokens=tokens.get("input", 0),
                        output_tokens=tokens.get("output", 0),
                        cache_read=tokens.get("cache_read", 0),
                        cache_write=tokens.get("cache_write", 0))