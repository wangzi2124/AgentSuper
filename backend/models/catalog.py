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

from models.builtin_catalog import BUILTIN_CATALOG as _BUILTIN_CATALOG  # noqa: F841 — 供 build_catalog 回退
from models.builtin_catalog import ZERO_COST as _ZERO_COST            # noqa: F841 — 供 probes / upsert / get_catalog 默认填充


def catalog_path() -> Path:
    """旧 model_catalog.json 覆盖文件（迁移备份，DB 生效后不再写入）。"""
    from models.catalog_db import catalog_json_path
    return catalog_json_path()


def catalog_db_path() -> Path:
    """模型目录数据库路径（当前单一事实来源）。"""
    from models.catalog_db import catalog_db_path as _dbp
    return _dbp()


def load_config() -> dict[str, Any]:
    """读取模型配置（DB 为单一事实来源；语义与原 JSON 一致）。"""
    from models.catalog_db import load_config as _load
    return _load()


def save_config(cfg: dict[str, Any]) -> None:
    """整体写回模型配置到数据库。"""
    from models.catalog_db import save_config as _save
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


def _db_builtin_entries() -> Optional[list[dict[str, Any]]]:
    """从 DB 读取 kind='builtin' 的内建条目；空库返回 None（触发回退常量）。"""
    from models.catalog_db import load_builtin_entries
    return load_builtin_entries() or None


def base_specs() -> dict[str, dict[str, Any]]:
    """内置基准：代码常量 + DB kind='builtin'（同 id 时 DB 覆盖代码）。

    代码常量永远兜底（长生命库中某内置模型被用户 override/删除后仍补齐规格），
    DB 行是被物化、可查可导的真实快照。
    """
    base: dict[str, dict[str, Any]] = {e["id"]: dict(e) for e in _BUILTIN_CATALOG}
    for db_entry in _db_builtin_entries() or []:
        mid = db_entry.get("id")
        if mid:
            base[mid] = db_entry
    return base


def build_catalog(cfg: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """内置目录（DB+常量双源）+ 数据库覆盖（overrides 深合并 / extra 追加）。

    - 基准 = 代码常量，同一 id 被 DB kind='builtin' 行 supersede（可查可导）。
    - cfg 可注入内存配置（未落库前的自愈校验用），默认自 DB 读取。
    """
    by_id: dict[str, dict] = {
        mid: _deep_merge(dict(e), {})
        for mid, e in base_specs().items()
    }
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
    from models.catalog_db import _data_dir
    return str(_data_dir())

# ── Ollama 本地模型探测 ───────────────────────────────────────────────────
# 目录是「声明式」的（内置 + 覆盖文件），但真实环境里 ollama 装了哪些模型是运行时事实。
# 以 60s TTL 探测 ollama /api/tags，把「本机已装但目录未收录」的模型合并进目录快照，
# 这样前端下拉框与默认模型（.env LLM_MODEL，如 ollama/qwen2.5:3b）都能对上真环境。
_OLLAMA_TTL = 60.0
_ollama_cache: Optional[list[dict[str, Any]]] = None
_ollama_cache_time: float = 0.0
_ollama_probe_ok: bool = False
_ollama_installed: Optional[set[str]] = None
# /api/tags 里**所有**模型的真实能力/窗口（含已收录的内置 id）。过去 probe 会跳过已
# 收录 id，导致写错的内置 capabilities（如 mistral tool_use=False）永远得不到纠正
# —— 这里额外记录真实值，由 `_apply_ollama_native` 在目录快照组装后刷新（用户 override 优先）。
_ollama_native: dict[str, dict[str, Any]] = {}
_ollama_lock = threading.Lock()

# ── Ollama 运行时窗口（/api/ps）───────────────────────────────────────────
# `/api/tags`、`/api/show` 只给模型**原生上限**，不代表服务器实际运行的 num_ctx
# （OLLAMA_CONTEXT_LENGTH / 启动参数）。真正生效的窗口在 `/api/ps.context_length`，
# 但该接口只列**已加载**模型。策略：
#   - 每次解析前最多每 `_RUNTIME_CTX_TTL` 秒探一次 `/api/ps`（本地 HTTP，毫秒级）；
#   - 探到的值按模型 id 记住（last-known），模型被卸载后继续沿用，避免窗口跳变。
_RUNTIME_CTX_TTL = 30.0
_runtime_ctx_last: dict[str, int] = {}
_ps_cache: Optional[dict[str, int]] = None
_ps_cache_at: float = 0.0
_runtime_ctx_lock = threading.Lock()


def _explicit_context_override(model_id: str) -> int:
    """用户在「模型管理」显式设置的上下文长度（overrides[mid].context_length）。

    只有 overrides 里真的写了该字段才算显式；探测器写入目录的值不算。
    返回 0 表示未设置。
    """
    if not model_id:
        return 0
    try:
        ov = (load_config().get("overrides") or {}).get(model_id) or {}
        return max(0, int(ov.get("context_length") or 0))
    except Exception:  # noqa: BLE001 - 配置损坏按未设置处理
        return 0


def _ps_snapshot(force: bool = False) -> dict[str, int]:
    """`/api/ps` 快照：{模型名: 运行窗口}，30s 缓存；不可达返回 {}。"""
    global _ps_cache, _ps_cache_at
    now = time.time()
    with _runtime_ctx_lock:
        if not force and _ps_cache is not None and now - _ps_cache_at < _RUNTIME_CTX_TTL:
            return dict(_ps_cache)
        _ps_cache_at = now
    out: dict[str, int] = {}
    if local_endpoint_alive(_ollama_base_url()):
        try:
            import urllib.request

            req = urllib.request.Request(
                f"{_ollama_base_url()}/api/ps",
                headers={"User-Agent": "agentsuper/0.1"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for m in data.get("models") or []:
                try:
                    ctx = max(0, int(m.get("context_length") or 0))
                except (TypeError, ValueError):
                    ctx = 0
                for name in {str(m.get("name") or ""), str(m.get("model") or "")}:
                    if name:
                        out[name] = ctx
        except Exception:  # noqa: BLE001 - 未启动/超时按未知处理
            out = {}
    with _runtime_ctx_lock:
        _ps_cache = dict(out)
    return out


def probe_runtime_context_length(model_id: Optional[str], force: bool = False) -> int:
    """Ollama 模型的实际运行窗口（`/api/ps.context_length`），带 30s 探测节流。

    读不到（未加载/未启动）时返回上次观测值；从未观测过返回 0。
    """
    mid = normalize_model(model_id)
    if not is_ollama_model(mid):
        return 0
    name = get_ollama_model_name(mid)
    ctx = _ps_snapshot(force=force).get(name, 0)
    with _runtime_ctx_lock:
        if ctx > 0:
            _runtime_ctx_last[mid] = ctx
        return _runtime_ctx_last.get(mid, 0)


def forget_runtime_context(model_id: Optional[str], *, reprobe: bool = True) -> int:
    """丢弃某模型的 last-known 运行窗口（超窗错误后调用），可选立即强制重探。

    返回重探到的窗口（未加载/不可达时为 0）。`effective_context_length` 随后会
    回落到 `settings.ollama_num_ctx` 的保守值。
    """
    mid = normalize_model(model_id)
    if not is_ollama_model(mid):
        return 0
    with _runtime_ctx_lock:
        _runtime_ctx_last.pop(mid, None)
    if reprobe:
        return probe_runtime_context_length(mid, force=True)
    return 0


def runtime_context_lengths(model_ids: list[str]) -> dict[str, int]:
    """批量计算每个模型的**实际**窗口（一次 `/api/ps` 探测，供 UI 只读展示）。"""
    snap = _ps_snapshot()
    explicit = load_config().get("overrides") or {}
    out: dict[str, int] = {}
    for raw in model_ids:
        mid = normalize_model(raw) or raw
        if not is_ollama_model(mid):
            out[raw] = int(read_limits(mid).get("context_length") or 0)
            continue
        name = get_ollama_model_name(mid)
        ctx = snap.get(name, 0)
        if ctx > 0:
            with _runtime_ctx_lock:
                _runtime_ctx_last[mid] = ctx
        try:
            ov = int(((explicit.get(mid) or {})).get("context_length") or 0)
        except Exception:  # noqa: BLE001
            ov = 0
        if ov > 0:
            ctx = ov
        elif ctx <= 0:
            with _runtime_ctx_lock:
                ctx = _runtime_ctx_last.get(mid, 0)
        if ctx <= 0:
            try:
                from config import settings

                ctx = int(settings.ollama_num_ctx or 0)
            except Exception:  # noqa: BLE001
                ctx = 0
        out[raw] = ctx
    return out


def annotate_runtime_context(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给目录条目补只读字段 `runtime_context_length`（不改 context_length 语义）。"""
    ids = [str(e.get("id") or "") for e in entries if e.get("id")]
    try:
        runtime = runtime_context_lengths(ids)
    except Exception:  # noqa: BLE001 - 展示字段失败不影响目录
        runtime = {}
    out: list[dict[str, Any]] = []
    for e in entries:
        item = dict(e)
        mid = str(item.get("id") or "")
        if mid in runtime:
            item["runtime_context_length"] = runtime[mid]
        out.append(item)
    return out


def effective_context_length(model_id: Optional[str], fallback: int = 0) -> int:
    """调用链真正可用的上下文窗口。

    - Ollama：用户显式覆盖 > `/api/ps`（含 last-known）> `fallback`
      （通常是 `settings.ollama_num_ctx`；服务器未加载时的保守值）。
    - 其它：目录声明值（历史行为）。
    """
    mid = normalize_model(model_id)
    if not mid:
        return max(0, int(fallback or 0))
    if is_ollama_model(mid):
        explicit = _explicit_context_override(mid)
        if explicit > 0:
            return explicit
        runtime = probe_runtime_context_length(mid)
        if runtime > 0:
            return runtime
        if not fallback:
            try:
                from config import settings

                fallback = int(settings.ollama_num_ctx or 0)
            except Exception:  # noqa: BLE001
                fallback = 0
        return max(0, int(fallback or 0))
    return int(read_limits(mid).get("context_length") or 0) or max(0, int(fallback or 0))


def _ollama_base_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def _ollama_native_base(api_base: Optional[str]) -> str:
    """把用户填写的 Ollama 地址归一为原生 API 根（去掉 /v1、/api、/api/chat）。

    Ollama 同时提供原生 `/api/chat` 与 OpenAI 兼容 `/v1`。本系统走原生接口
    （工具调用、think 参数行为更可控），因此注册表里允许用户填任意一种写法。
    """
    base = str(api_base or "").strip().rstrip("/")
    for suffix in ("/api/chat", "/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    return base


def _ollama_tags_url(api_base: Optional[str]) -> str:
    base = _ollama_native_base(api_base)
    return f"{base}/api/tags" if base else ""


def _provider_type(reg: Optional[dict[str, Any]]) -> str:
    value = str((reg or {}).get("type") or "openai").strip().lower()
    return "ollama" if value == "ollama" else "openai"


def is_ollama_model(model_id: Optional[str]) -> bool:
    """判断模型 ID 是否为 Ollama 模型（支持 ollama/ 和 ollama_chat/ 前缀）。

    ollama_chat/ 是 LiteLLM 推荐的前缀，可正确处理工具调用；
    ollama/ 是旧前缀，流式工具调用有已知问题。
    """
    if not model_id:
        return False
    return model_id.startswith("ollama/") or model_id.startswith("ollama_chat/")


def get_ollama_model_name(model_id: str) -> str:
    """从 Ollama 模型 ID 提取模型名（去掉 ollama/ 或 ollama_chat/ 前缀）。"""
    if model_id.startswith("ollama_chat/"):
        return model_id[len("ollama_chat/"):]
    if model_id.startswith("ollama/"):
        return model_id[len("ollama/"):]
    return model_id


# 本地端点（ollama / vLLM / LM Studio）快速预检。
# 为什么需要：在装了 TUN/透明代理（mihomo/clash 等）的机器上，指向**没有监听**的
# localhost 端口的连接不会立刻 refused，而是被静默丢包 → TCP connect 一直挂到超时。
# 于是「ollama 没启动」这种最常见状态反而最贵：urlopen(timeout=3) 实测赔 ~4.1s，
# 而 ollama 的 /api/tags 与 ollama provider 的 /v1/models 各赔一次 → `GET /api/models`
# 冷启动 ~8.4s（串行时更高），该接口又在前端每次整页加载的关键路径上。
# 这里先用 250ms 的裸 TCP connect 探一下端口，端口没人听就直接放弃 HTTP 探测。
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}
_LOCAL_PORT_PROBE_TIMEOUT = 0.25


def _split_host_port(base_url: str) -> tuple[str, int] | None:
    """从 http(s)://host:port 形式的 base_url 解析 (host, port)；非本地/无端口返回 None。"""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(base_url if "://" in base_url else "http://" + base_url)
        host = (parsed.hostname or "").lower()
        if host not in _LOCAL_HOSTS:
            return None
        return host, int(parsed.port or (443 if parsed.scheme == "https" else 80))
    except Exception:  # noqa: BLE001
        return None


def local_endpoint_alive(base_url: str, timeout: float = _LOCAL_PORT_PROBE_TIMEOUT) -> bool:
    """本地 base_url 的端口是否有监听。非本地端点一律返回 True（不拦远程探测）。"""
    target = _split_host_port(base_url)
    if target is None:
        return True
    import socket

    host, port = target
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except Exception:  # noqa: BLE001
        return False
    try:
        sock.close()
    except Exception:  # noqa: BLE001
        pass
    return True


def probe_ollama_models(known_ids: set[str], force: bool = False) -> list[dict[str, Any]]:
    """探测本机 ollama 已安装模型，返回「目录未收录」的增量条目。

    ollama 未启动/不可达/超时一律返回 []（不抛异常，不阻塞启动）；
    成功探测结果按 TTL 缓存，避免每个请求都去打 /api/tags。
    """
    global _ollama_cache, _ollama_cache_time, _ollama_probe_ok, _ollama_installed, _ollama_native
    now = time.time()
    if not force and _ollama_cache is not None and now - _ollama_cache_time < _OLLAMA_TTL:
        return _ollama_cache
    with _ollama_lock:
        if not force and _ollama_cache is not None and now - _ollama_cache_time < _OLLAMA_TTL:
            return _ollama_cache
        # 本地 ollama 没监听就不必再发 HTTP（被 TUN 代理丢包时会赔满超时，见 local_endpoint_alive）
        if not local_endpoint_alive(_ollama_base_url()):
            _ollama_cache, _ollama_cache_time, _ollama_probe_ok = [], now, False
            _ollama_installed = None
            _ollama_native = {}
            return []
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{_ollama_base_url()}/api/tags",
                headers={"User-Agent": "agentsuper/0.1"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            extras: list[dict[str, Any]] = []
            native: dict[str, dict[str, Any]] = {}
            for m in (data.get("models") or []):
                name = (m.get("name") or "").strip()
                if not name:
                    continue
                # 使用 ollama_chat/ 前缀（LiteLLM 推荐，工具调用兼容性更好）
                mid = f"ollama_chat/{name}"
                family = name.split(":", 1)[0]
                # [能力透传] /api/tags 的 capabilities 列出入参能力（vision/completion/tools/thinking），
                # 映射为本系统能力键：tools→tool_use、thinking→reasoning。老版 ollama 无该字段
                # → None（未知）：未收录模型按默认关；已收录条目保留内置声明，不据此误降级。
                raw_caps = set(m.get("capabilities") or [])
                capabilities = {
                    "tool_use": "tools" in raw_caps,
                    "vision": "vision" in raw_caps,
                    "reasoning": "thinking" in raw_caps,
                } if raw_caps else None
                # [真实窗口] 新版 ollama /api/tags 在 details.context_length 给出模型原生
                # 窗口（如 qwen3.5:4b=262144）。老版本缺失 → 回退 32768/8192 保守值。
                details = m.get("details") or {}
                try:
                    native_ctx = int(details.get("context_length") or 0)
                except (TypeError, ValueError):
                    native_ctx = 0
                context_length = native_ctx if native_ctx > 0 else 32768
                # 已收录的内置/覆盖条目：不改条目结构，只记录真实能力/窗口，
                # 交由 `_apply_ollama_native` 刷新过期的内置默认值（用户 override 优先）。
                native[mid] = {
                    "capabilities": capabilities,
                    "context_length": native_ctx,
                    "limits": ({"max_output_tokens": native_ctx} if native_ctx > 0 else None),
                }
                if mid in known_ids:
                    continue
                extras.append({
                    "id": mid,
                    "provider": "ollama",
                    "family": family,
                    "name": f"Ollama {family}",
                    "description": "本地 Ollama 模型（探测自 /api/tags）",
                    "capabilities": capabilities or {"tool_use": False, "vision": False, "reasoning": False},
                    "context_length": context_length,
                    # 输出上限跟随真实窗口：resolve_max_output_tokens 会与全局
                    # LLM_MAX_TOKENS 取小（大窗模型 → 用全局预算，小窗模型 → 收窄）。
                    "limits": {"max_output_tokens": context_length if native_ctx > 0 else 8192},
                    "cost": dict(_ZERO_COST),
                })
            _ollama_cache, _ollama_cache_time, _ollama_probe_ok = extras, now, True
            _ollama_native = native
            _ollama_installed = {
                (m.get("name") or "").strip()
                for m in (data.get("models") or [])
                if (m.get("name") or "").strip()
            }
            return extras
        except Exception:
            _ollama_cache, _ollama_cache_time, _ollama_probe_ok = [], now, False
            _ollama_installed = None
            _ollama_native = {}
            return []


def _ollama_installed_names() -> Optional[set[str]]:
    """本机 ollama 已安装模型名集合（探测 /api/tags，TTL 缓存）。

    返回 None 表示探测失败（ollama 未启动/不可达）——调用方应放行；
    返回 set（可能为空）表示探测成功——空集即「任何 ollama/xxx 都未安装」。
    """
    probe_ollama_models(set(), force=False)
    if not _ollama_probe_ok:
        return None
    return _ollama_installed or set()


def ollama_model_installed(model_id: Optional[str]) -> bool:
    """ollama/<name> 或 ollama_chat/<name> 是否已在本机安装（按 /api/tags 探测）。

    - 非 ollama 前缀 / 空模型 → True（不拦截）；
    - 探测失败（ollama 未启动/不可达）→ True（放行，交给 litellm 报原始错误，
      避免把「服务未启动」误判成「模型未安装」）；
    - 探测成功但目录里没有该模型名 → False（用户需 `ollama pull <name>`）。
    """
    if not model_id:
        return True
    if not is_ollama_model(model_id):
        return True
    name = get_ollama_model_name(model_id)
    if not name:
        return True
    installed = _ollama_installed_names()
    if installed is None:
        return True
    return name in installed


def _apply_ollama_native(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用 /api/tags 的真实能力/窗口刷新已收录的 ollama 条目（用户 override 优先）。

    背景：内置目录里部分模型的 capabilities 写死（如 mistral/llama3.1 曾为
    tool_use=False），而 `probe_ollama_models` 会跳过已收录 id → 真实的 `tools` 能力
    永远进不来，这类模型被 `is_weak_model` 当弱模型、不挂工具。这里在目录快照组装后，
    用探测到的真实值刷新**未被 override 的**字段：

      - capabilities：仅当 /api/tags 明确给出 capabilities 时采用（老版 ollama 无该
        字段 → 保留内置声明，绝不误降级）；
      - context_length / limits：仅当探测到原生窗口（>0）时采用（`resolve_*` 仍会与
        全局取小，不会放大）。
    """
    if not _ollama_native:
        return entries
    overrides = load_config().get("overrides") or {}
    out: list[dict[str, Any]] = []
    for e in entries:
        n = _ollama_native.get(e.get("id"))
        if not n:
            out.append(e)
            continue
        ov = overrides.get(e["id"]) or {}
        e2 = dict(e)
        nat_caps = n.get("capabilities")
        if nat_caps and "capabilities" not in ov:
            e2["capabilities"] = dict(nat_caps)
        if n.get("context_length") and "context_length" not in ov:
            e2["context_length"] = n["context_length"]
        if n.get("limits") and "limits" not in ov:
            e2["limits"] = dict(n["limits"])
        out.append(e2)
    return out


def get_catalog(force_reload: bool = False) -> list[dict[str, Any]]:
    """目录快照（按数据目录键的线程安全缓存；force_reload=True 重新读库 + 重探测）。"""
    global _ollama_native
    key = _cache_key()
    cached = _catalog_cache.get(key)
    if cached is None or force_reload:
        with _catalog_lock:
            cached = _catalog_cache.get(key)
            if cached is None or force_reload:
                static = build_catalog()
                known = {e["id"] for e in static}
                # 复位真实值快照：由紧随其后的探测重新填充（探测被 stub 时保持空，不影响测试）。
                _ollama_native = {}
                probe = probe_ollama_models(known, force=force_reload)
                known |= {e["id"] for e in probe}
                probe = probe + probe_configured_providers(known, force=force_reload)
                merged = _apply_ollama_native(static + probe)
                # 隐藏集合：用户显式删除过的模型 id（含 Provider 探测增量/内置条目）。
                # 探测会在每次重建时把端点上仍在线的模型自动注册回来，仅删 overrides/
                # extra 会被下个请求「复活」；这里统一过滤，使删除真正持久。
                hidden = read_hidden()
                if hidden:
                    merged = [e for e in merged if e.get("id") not in hidden]
                cached = merged
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


def read_hidden() -> set[str]:
    """返回用户隐藏（已删除）的模型 id 集合。

    用于抑制 Provider 探测增量与内置条目在目录快照里出现：删除一个端点仍在线的
    探测模型时只清 overrides/extra 无效（下次重建探测又把它注册回来），故删除同时
    记入 `hidden`，由 `get_catalog` 过滤。
    """
    cfg = load_config()
    return {str(x) for x in (cfg.get("hidden") or []) if x}


def upsert_provider(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """新增/更新一个 provider（前端模型管理写入 model_catalog.db）。

    `type` 决定调用协议：
    - `openai`（默认）：`{name}/{model}` 直接交给 litellm，按 OpenAI 兼容服务处理；
    - `ollama`：使用 Ollama 原生 `/api/chat`，模型 id 仍为 `{name}/{model}`，
      调用时映射为 `ollama_chat/{model}`，并支持 `/api/tags` 自动探测模型。
    """
    cfg = load_config()
    providers = dict(cfg.get("providers") or {})
    entry = dict(providers.get(name) or {})
    for k in ("label", "api_base", "api_key", "enabled", "type"):
        if k in data and data[k] is not None:
            entry[k] = data[k]
    entry.setdefault("label", name)
    entry.setdefault("api_key", "")
    entry.setdefault("enabled", True)
    entry.setdefault("api_base", "")
    ptype = _provider_type(entry)
    if ptype == "ollama" and name in ("ollama", "ollama_chat"):
        raise ValueError(
            "Provider 名称不能使用保留前缀 ollama / ollama_chat（本地 Ollama 已占用）；"
            "Ollama Cloud 请使用 ollama-cloud 等名称"
        )
    entry["type"] = ptype
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
    # 用户显式新增/重新添加该 id → 解除隐藏（提供「删除后想找回」的逃生舱）。
    hidden = {str(x) for x in (cfg.get("hidden") or []) if x}
    if mid in hidden:
        hidden.discard(mid)
        cfg["hidden"] = sorted(hidden)
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
    """删除自定义模型（overrides / extra 中的同名条目），并隐藏防「探测复活」。

    仅清 overrides/extra 不足以真正删除：对于 Provider（vLLM/LM Studio/私服）探测
    自动注册的模型，端点 `/v1/models` 仍列出该模型，下一次 `get_catalog` 探测会把它
    重新注册进目录 —— 表现为「删除不了 / 删了又回来」。因此这里同时把 id 记入
    `hidden`，由 `get_catalog` 在快照组装时过滤；内置目录条目同样可被隐藏。
    """
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
    hidden = {str(x) for x in (cfg.get("hidden") or []) if x}
    if mid not in hidden:
        hidden.add(mid)
        cfg["hidden"] = sorted(hidden)
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
    hidden = {str(x) for x in (cfg.get("hidden") or []) if x}
    for key in ("default_model", "small_model", "image_caption_model"):
        v = cfg.get(key)
        if v and (str(v) not in ids or str(v) in hidden):
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
             "context_length", "limits", "cost", "default", "tokenizer", "thinking"}
    return {k: v for k, v in entry.items() if k in known and v is not None}


# ── 自定义 Provider 模型探测（启动自动注册）────────────────────────────────
_PROBE_TTL = 60.0
# 单个 provider 的 /v1/models 探测超时。探测只用于把「provider 上有但目录未收录」的模型
# 补进下拉框，属锦上添花，不值得让请求等太久——调小它可降低不可达端点的拖累。
_PROBE_TIMEOUT = float(os.environ.get("MODEL_PROBE_TIMEOUT", "4") or 4)
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
    base = str(api_base or "").rstrip("/")
    if not base:
        _probe_cache[provider_name] = (now, [])
        return []
    # 本地端点没人监听就直接放弃（被 TUN 代理丢包时 HTTP 探测会赔满超时，见 local_endpoint_alive）
    if not local_endpoint_alive(base):
        _probe_cache[provider_name] = (now, [])
        return []
    try:
        import urllib.request
        # 兼容 api_base 已带 /v1 的写法（OpenAI 兼容地址通常就是 <host>/v1，前端占位符
        # 就是 http://127.0.0.1:8001/v1）：先去掉尾部 /v1 再拼 /v1/models，否则会请求
        # <host>/v1/v1/models → 404 → 新增 Provider 后模型永远不自动注册（"模型数"恒 0）。
        norm = base[:-3] if base.lower().endswith("/v1") else base
        url = norm + "/v1/models"
        req = urllib.request.Request(url, headers={"User-Agent": "agentsuper/0.1",
                                                   **(dict({"Authorization": f"Bearer {api_key}"}) if api_key else {})})
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
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


def probe_ollama_provider_models(provider_name: str, api_base: str, api_key: str,
                                 known_ids: set[str], force: bool = False) -> list[dict[str, Any]]:
    """探测注册表中 `type=ollama` 的 Provider（云端或远程 Ollama）的 `/api/tags`。

    与本地 `probe_ollama_models` 的区别：使用该 Provider 自己的地址与 Bearer key，
    不写入本地 Ollama 的全局缓存/已安装集合。失败返回 []，不阻塞启动或模型列表。
    """
    now = time.time()
    cache_key = f"ollama:{provider_name}"
    cached = _probe_cache.get(cache_key)
    if cached and not force and now - cached[0] < _PROBE_TTL:
        return cached[1]
    url = _ollama_tags_url(api_base)
    if not url:
        _probe_cache[cache_key] = (now, [])
        return []
    if not local_endpoint_alive(_ollama_native_base(api_base)):
        _probe_cache[cache_key] = (now, [])
        return []
    headers = {"User-Agent": "agentsuper/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        extras: list[dict[str, Any]] = []
        for m in (data.get("models") or []):
            name = (m.get("name") or m.get("model") or "").strip()
            if not name:
                continue
            full = f"{provider_name}/{name}"
            if full in known_ids:
                continue
            family = name.split(":", 1)[0]
            raw_caps = set(m.get("capabilities") or [])
            capabilities = {
                "tool_use": "tools" in raw_caps,
                "vision": "vision" in raw_caps,
                "reasoning": "thinking" in raw_caps,
            } if raw_caps else {"tool_use": True, "vision": False, "reasoning": False}
            details = m.get("details") or {}
            try:
                native_ctx = int(details.get("context_length") or 0)
            except (TypeError, ValueError):
                native_ctx = 0
            context_length = native_ctx if native_ctx > 0 else 32768
            extras.append({
                "id": full,
                "provider": provider_name,
                "family": family,
                "name": f"{provider_name} {family}",
                "description": f"探测自 {provider_name} /api/tags",
                "capabilities": capabilities,
                "context_length": context_length,
                "limits": {"max_output_tokens": 8192},
                "cost": dict(_ZERO_COST),
            })
        _probe_cache[cache_key] = (now, extras)
        return extras
    except Exception:
        _probe_cache[cache_key] = (now, [])
        return []


def probe_configured_providers(known_ids: set[str], force: bool = False) -> list[dict[str, Any]]:
    """对所有 enabled 的自定义 provider 做 /v1/models 探测，合并增量条目。

    并行探测：每个 provider 的探测是独立的外网 HTTP（timeout=_PROBE_TIMEOUT），
    串行时端点不可达会把耗时累加（实测 3 个 provider 串行 → 8.7s，阻塞
    `GET /api/models`，而该接口在前端每次整页加载的 critical path 上）。
    并行后总耗时 = 最慢的那一个。`known_ids` 只读，无需跨线程共享写状态。
    """
    targets = [
        (name, p)
        for name, p in read_providers().items()
        if p.get("enabled", True) and p.get("api_base")
    ]
    if not targets:
        return []
    if len(targets) == 1:
        name, p = targets[0]
        try:
            if _provider_type(p) == "ollama":
                return probe_ollama_provider_models(
                    name, p.get("api_base"), p.get("api_key") or "", known_ids, force=force
                )
            return probe_provider_models(name, p.get("api_base"), p.get("api_key") or "", known_ids, force=force)
        except Exception:
            return []

    from concurrent.futures import ThreadPoolExecutor

    def _one(item: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
        name, p = item
        try:
            if _provider_type(p) == "ollama":
                return probe_ollama_provider_models(
                    name, p.get("api_base"), p.get("api_key") or "", known_ids, force=force
                )
            return probe_provider_models(name, p.get("api_base"), p.get("api_key") or "", known_ids, force=force)
        except Exception:
            return []

    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(targets), 8), thread_name_prefix="model-probe") as pool:
        for res in pool.map(_one, targets):
            out.extend(res)
    return out


def _normalize_deepseek_thinking(value: Any) -> str:
    """归一化 DEEPSEEK_THINKING：enabled / disabled / adaptive（其它/空 → adaptive）。"""
    v = str(value or "").strip().lower()
    if v in ("enabled", "on", "true", "1"):
        return "enabled"
    if v in ("disabled", "off", "false", "0"):
        return "disabled"
    return "adaptive"


def provider_api(model_id: Optional[str]) -> dict[str, Any]:
    """解析模型对应的调用凭证：{api_base, api_key, is_ollama, options}。优先级：

    1) model 的 provider 命中注册表（providers.<name>.api_base/api_key）；
    2) ollama 前缀 → OLLAMA_HOST/api_key="ollama"（沿用 litellm 自动发现）；
    3) 未注册的自定义 provider → 回落 settings.llm_api_base/llm_api_key。

    `options`：需要透传给 litellm 的 provider 特定参数。Ollama 默认 num_ctx=2048，
    超过窗口的往期历史会被推理服务截断（模型答"记不清/无历史"），因此必须显式
    放大 num_ctx（settings.ollama_num_ctx）；条目声明的 `context_length` 会**收窄**该值
    （B5：模型自身窗口更小时以条目为准，避免按错误窗口估预算）；带 deterministic 能力的
    provider 也可在此扩展。

    `think`：Ollama 思考模型（qwen3.5 等）。开关优先级：模型条目的 `thinking` 字段
    （前端「模型管理」每模型「思考模式」，enabled/disabled/adaptive）> 全局 `OLLAMA_THINKING`
    环境变量 > 默认 disabled。litellm+ollama 下 think=True 会把最终回答全塞进
    reasoning_content、content 恒空（工具调用也进 reasoning，工具循环报废）；disabled→
    think=False（干净 content + 标准 tool_calls），enabled→think=True（保留思考），
    adaptive→不下发交服务端默认。仅对声明了 reasoning 能力的模型或显式设了 `thinking` 的
    模型生效。
    """
    from config import settings
    mid = normalize_model(model_id)
    if not mid:
        return {"api_base": settings.llm_api_base, "api_key": settings.llm_api_key,
                "is_ollama": False, "litellm_model": None, "provider_type": "openai", "options": None}
    provider = mid.split("/", 1)[0] if "/" in mid else ""
    reg = read_providers().get(provider)
    # 注册表中显式声明 type=ollama 的 Provider（如 Ollama Cloud）：走原生 /api/chat，
    # 但模型 id 保持 `{provider}/{name}`，避免与本地 ollama/、ollama_chat/ 前缀冲突。
    if reg and _provider_type(reg) == "ollama":
        name = mid.split("/", 1)[1] if "/" in mid else mid
        return {
            "api_base": _ollama_native_base(reg.get("api_base")) or None,
            "api_key": (reg.get("api_key") or "").strip() or "ollama",
            "is_ollama": True,
            "litellm_model": f"ollama_chat/{name}",
            "provider_type": "ollama",
            "options": None,
        }
    # 支持 ollama/ 与 ollama_chat/ 两种前缀（ollama_chat/ 是 LiteLLM 推荐的前缀）
    if is_ollama_model(mid):
        # [运行时窗口] 默认**不下发** num_ctx，让 Ollama 用它自己的配置
        # （OLLAMA_CONTEXT_LENGTH / 启动参数）。只有用户在「模型管理」显式设置
        # 过上下文长度时才覆盖服务器；否则旧逻辑会把服务器设的 16k 硬压回 8192，
        # prompt 超窗后由推理服务静默截断 → 表现为“模型忘记历史”。
        opts: dict[str, Any] = {}
        explicit_ctx = _explicit_context_override(mid)
        if explicit_ctx > 0:
            opts["num_ctx"] = explicit_ctx
        caps = read_capabilities(mid)
        entry = lookup(mid)
        # 思考开关优先级：模型条目 `thinking`（前端「模型管理」每模型配置）
        #   > 全局 OLLAMA_THINKING 环境变量 > 默认 disabled。
        # **默认 disabled**：litellm+ollama 下 think=True 会把最终回答全塞进
        # reasoning_content、content 恒空、工具调用也进 reasoning（工具循环报废），
        # 与 DeepSeek thinking 同理故默认关闭。
        #   enabled→think=True；disabled→think=False；adaptive→不传（交服务端默认）。
        per_model = (entry or {}).get("thinking")
        if per_model or caps.get("reasoning"):
            raw_mode = per_model or getattr(settings, "ollama_thinking", "disabled")
            _ot = _normalize_deepseek_thinking(raw_mode)
            if _ot in ("enabled", "disabled"):
                opts["think"] = (_ot == "enabled")
        # 连接凭证**只认前端注册表**（「模型管理」页 providers 表，upsert_provider 写入）；
        # 后端 .env 不参与 ollama 解析（不回落 LLM_API_BASE/LLM_API_KEY ——
        # 否则 api_base=None 被 .env 的 DeepSeek 端点顶掉、key 落成占位 "ollama"，
        # 请求打到 DeepSeek 网关报 "Your api key: ****lama is invalid"）。
        # 注册表未配 api_base → None 交 litellm 原生发现（OLLAMA_HOST 环境变量 → localhost:11434）。
        reg_o = read_providers().get("ollama") or {}
        _ob = (reg_o.get("api_base") or "").strip()
        _ok = (reg_o.get("api_key") or "").strip()
        # [OLLAMA_API_BASE 双保险] 规格：同时设置 api_base 参数和 OLLAMA_API_BASE 环境变量
        # 最稳妥（ChatLiteLLM 某些版本内部探测会忽略构造参数转而读环境变量）。
        # 注册表未配 api_base 时读 OLLAMA_API_BASE（其次 OLLAMA_HOST）兜底。
        if not _ob:
            _ob = (os.environ.get("OLLAMA_API_BASE") or os.environ.get("OLLAMA_HOST") or "").strip()
        return {"api_base": _ob or None, "api_key": _ok or "ollama", "is_ollama": True,
                "litellm_model": None, "provider_type": "ollama", "options": opts}
    # DeepSeek 思考模型（V4.1-Flash / V4-Pro 默认开思考）：按「推理模型」能力 + 全局
    # DEEPSEEK_THINKING 开关下发 thinking。**默认 disabled** —— 实测 agentic 工具循环里
    # 思考会吃光 max_tokens（reasoning==ct==上限、finish=length、零工具/零正文），
    # 关闭后 finish=stop 正常产出。enabled 时的 reasoning_content 回填依赖
    # litellm_vision_patch.install_litellm_reasoning_patch（修「思考模式多轮流式报错」）。
    # adaptive → 不显式下发（options=None），交服务端默认。
    options = None
    provider = mid.split("/", 1)[0] if "/" in mid else ""
    if provider == "deepseek" and read_capabilities(mid).get("reasoning"):
        _tt = _normalize_deepseek_thinking(getattr(settings, "deepseek_thinking", "disabled"))
        if _tt in ("enabled", "disabled"):
            options = {"thinking": {"type": _tt}}
    reg = read_providers().get(provider)
    if reg:
        return {"api_base": reg.get("api_base") or settings.llm_api_base,
                "api_key": reg.get("api_key") or settings.llm_api_key,
                "is_ollama": False, "litellm_model": None,
                "provider_type": _provider_type(reg), "options": options}
    return {"api_base": settings.llm_api_base, "api_key": settings.llm_api_key,
            "is_ollama": False, "litellm_model": None, "provider_type": "openai", "options": options}


def litellm_extra_kwargs(creds: Optional[dict]) -> dict:
    """把 provider_api() 的 `options` 展开成 litellm 顶层 kwargs。

    litellm 对 Ollama 只认顶层 `num_ctx`（非嵌套 `options`：transformation 会
    丢弃任意 `options` 键，导致 num_ctx 不生效、推理服务默认 2048 窗口截断历史）。
    `think` 同理需顶层传（Qwen3.5 思考模型按「推理模型」能力显式开关）。
    `thinking`（DeepSeek）同理需顶层传：litellm 的 deepseek transformation 只在
    optional_params 里读到 `thinking={"type":"enabled"}` 才回填 reasoning_content。
    无 options（None）返回 {}，避免把 num_ctx=None 透传给其它 provider。
    """
    opts = (creds or {}).get("options") or {}
    out: dict[str, Any] = {}
    if opts.get("num_ctx") is not None:
        out["num_ctx"] = opts["num_ctx"]
    if opts.get("think") is not None:
        out["think"] = bool(opts["think"])
    thinking = opts.get("thinking")
    if thinking is not None:
        # litellm 的 deepseek transformation（`_map_openai_params`）**只保留**
        # thinking={"type":"enabled"}，disabled/其它一律被 pop 丢弃 → 顶层传 disabled
        # 根本关不掉思考（实测仍烧满 16_384 reasoning）。必须走 `extra_body` 直接注入
        # 请求体才生效。enabled 仍走顶层：可同时触发 litellm 的 reasoning_content 回填。
        if isinstance(thinking, dict) and thinking.get("type") == "disabled":
            out.setdefault("extra_body", {})["thinking"] = thinking
        else:
            out["thinking"] = thinking
    return out


def provider_config_hint(model_id: Optional[str], *, creds: Optional[dict] = None) -> str:
    """检测模型对应的 Provider 是否已配置；未配置则返回友好中文提示，已配置返回 ""。

    判定口径（与 provider_api 的解析优先级一致）：
      - 无模型 / ollama 前缀 / provider 命中注册表且带 api_base → 视为已配置；
      - 否则走 settings.llm_api_base/api_key 兜底：api_key 为空 AND
        （api_base 为空 或 api_base 是 ollama 本地（localhost/127.0.0.1:11434）或默认
        deepseek 端点）→ 说明用户根本没配服务商，返回提示而非把它当自定义 provider 直连。
    提示文案面向「模型管理」页配置 Provider（api_base + api_key），也提及 .env 兜底。
    """
    from config import settings

    mid = normalize_model(model_id)
    if not mid:
        return ""
    # 支持 ollama/ 和 ollama_chat/ 两种前缀
    if is_ollama_model(mid):
        # ollama 前缀：provider 本身无需配置；但模型必须已安装
        mname = get_ollama_model_name(mid)
        if mname and not ollama_model_installed(mid):
            return ("本地 Ollama 未安装模型「%s」。请在终端执行 `ollama pull %s` "
                    "安装后重试，或在「模型管理」页切换为其它已安装模型。"
                    % (mname, mname))
        return ""
    provider = mid.split("/", 1)[0] if "/" in mid else ""
    if not provider:
        return ""
    reg = read_providers().get(provider)
    if reg and _provider_type(reg) == "ollama":
        base = (reg.get("api_base") or "").strip()
        key = (reg.get("api_key") or "").strip()
        if not base:
            return ("未配置 Ollama 服务地址：请在「模型管理」页为 Provider「%s」填写 api_base"
                    "（云端 https://ollama.com，本地 http://localhost:11434）。" % provider)
        if "ollama.com" in base.lower() and not key:
            return ("Ollama Cloud 需要 API Key：请在「模型管理」页为 Provider「%s」填写 api_key"
                    "（Ollama 控制台生成的 OLLAMA_API_KEY）。" % provider)
        return ""
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
        return "未配置模型服务商（Provider）「%s」，请在「模型管理」页为该 Provider 配置 api_base 与 api_key。" % provider
    return ""


def normalize_llm_exception(exc: BaseException, model_id: Optional[str] = None) -> str:
    """把 litellm/底层调用异常归一为「面向模型服务商配置」的友好中文提示；无法识别的返回空串。

    识别两类高频可操作错误：
      1) Ollama 模型未安装：`model 'qwen2.5:7b' not found`（litellm 包成
         APIConnectionError: OllamaException - {"error": ...}）→ 提示 `ollama pull`。
      2) Provider 服务/密钥不可用（连接失败、401、404、NoneType API key 等）→
         提示到「模型管理」页核对 api_base/api_key。
    `model_id` 可选：提供时优先用 ollama_model_installed 探测做前置判定（比解析报错更稳）。
    """
    msg = str(exc)
    if not msg:
        return ""
    # 1) Ollama 模型未安装 —— 解析报错文本里的 `'<name>'` / `model "<name>"` 引用
    if model_id:
        provider, _, name = str(model_id).partition("/")
        if provider == "ollama" and name:
            if not ollama_model_installed(model_id):
                return ("本地 Ollama 未安装模型「%s」。请在终端执行 `ollama pull %s` "
                        "安装后重试，或在「模型管理」页切换为其它已安装模型。"
                        % (name, name))
            if "not found" in msg.lower() or "ollamaexception" in msg.lower():
                return ("本地 Ollama 未能加载模型「%s」：%s。请确认已执行 `ollama pull %s` "
                        "且 Ollama 服务正在运行，或切换为其它模型。"
                        % (name, msg.strip()[:200], name))
        else:
            # 非 ollama provider：报错文本若含 model not found，同样给出统一提示
            if "not found" in msg.lower() and ("model" in msg.lower() or "404" in msg.lower()):
                return ("模型「%s」不存在（%s）。请到「模型管理」页核对模型 id 或切换为目录内可用模型。"
                        % (str(model_id), msg.strip()[:200]))
    if "not found" in msg.lower() and ("model '" in msg or 'model "' in msg or "ollama" in msg.lower()):
        _name = "（见上方报错）"
        return ("模型不存在：%s。若是本地 Ollama，请执行 `ollama pull <模型名>` 安装；"
                "若是云端服务，请到「模型管理」页核对模型 id。" % msg.strip()[:200])
    # 2) 连接/鉴权类 —— 报错文本常见的连接拒绝、401/403/404
    low = msg.lower()
    if ("api key" in low or "apikey" in low or "401" in low or "403" in low or
            "authentication" in low or "authorization" in low):
        return ("模型服务商鉴权失败：%s。请到「模型管理」页核对当前模型的 api_base 与 api_key，"
                "或检查 .env 的 LLM_API_BASE / LLM_API_KEY。" % msg.strip()[:200])
    if ("connect" in low or "connection" in low or "refused" in low or
            "timeout" in low or "unreachable" in low or "network" in low):
        return ("无法连接到模型服务商：%s。请确认服务地址（api_base）可达、网络已放行，"
                "本地 Ollama 请确认已 `ollama serve` 启动。" % msg.strip()[:200])
    return ""


def provider_models_source() -> list[dict[str, Any]]:
    """当前可用的提供商信息（前端模型管理面板）。"""
    out = []
    for name, p in read_providers().items():
        out.append({"provider": name, "label": p.get("label") or name,
                    "api_base": p.get("api_base") or "", "api_key": p.get("api_key") or "",
                    "enabled": bool(p.get("enabled", True)),
                    "type": _provider_type(p),
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


def read_capabilities(model_id: Optional[str]) -> dict[str, bool]:
    """读取模型声明的能力（tool_use/vision/reasoning）。

    - 目录收录（内置/覆盖/探测增量）→ 按要求能力键读取；
    - 未收录模型 → 空 dict（调用方按默认值处理，不臆断能力）。
    """
    if not model_id:
        return {}
    entry = lookup(model_id)
    if not entry:
        return {}
    caps = entry.get("capabilities") or {}
    return {
        "tool_use": bool(caps.get("tool_use", False)),
        "vision": bool(caps.get("vision", False)),
        "reasoning": bool(caps.get("reasoning", False)),
    }


def read_limits(model_id: Optional[str]) -> dict[str, int]:
    """读取模型条目声明的窗口/输出上限 → `{"context_length": int, "max_output_tokens": int}`。

    B5/B6：这两个字段过去只写不读（用户在「模型管理」改了没有任何效果）。现在它们
    作为**每模型覆盖**被真正消费（见 `resolve_context_length` / `resolve_max_output_tokens`）。
    未收录模型或未声明 → 0（调用方回落到全局 settings）。
    """
    out = {"context_length": 0, "max_output_tokens": 0}
    if not model_id:
        return out
    entry = lookup(model_id)
    if not entry:
        return out
    try:
        ctx = int(entry.get("context_length") or 0)
    except (TypeError, ValueError):
        ctx = 0
    limits = entry.get("limits") or {}
    try:
        mx = int(limits.get("max_output_tokens") or 0)
    except (TypeError, ValueError):
        mx = 0
    out["context_length"] = ctx if ctx > 0 else 0
    out["max_output_tokens"] = mx if mx > 0 else 0
    return out


def resolve_context_length(model_id: Optional[str], fallback: int) -> int:
    """模型的上下文窗口（**模型条目声明优先**，否则用全局 `.env` 值）。

    B5：`context_length` 声明值小于全局值时生效 —— 目录/覆盖文件里写小窗口（如本地
    32768 的 ollama 模型）是用户/维护者的明确约束，不能被 `.env MAX_CONTEXT_TOKENS=160000`
    这种为云端大窗模型设的全局值反向撑大（否则压缩阈值、truncation 预算全按错的窗口算）。
    声明值更大时不放大全局值：`.env` 仍是全局权威，避免用户漏改条目时静默改变行为。
    """
    global_ctx = max(0, int(fallback or 0))
    declared = read_limits(model_id)["context_length"]
    if not declared:
        return global_ctx
    if not global_ctx:
        return declared
    return min(global_ctx, declared)


def resolve_max_output_tokens(model_id: Optional[str], fallback: int) -> int:
    """每次调用的输出上限（**模型条目声明与全局值取小**，绝不放大）。

    B6：`limits.max_output_tokens` 现在真的被 `_llm_call` 消费。取 `min` 而非「条目覆盖」
    是刻意的 —— 模型自身输出上限（目录值）永远不能超过 `.env LLM_MAX_TOKENS` 这个全局
    成本/延迟预算；条目只起到「这个模型更小 → 收窄」的上限作用，未声明 → 用全局值。
    """
    global_mx = max(0, int(fallback or 0))
    declared = read_limits(model_id)["max_output_tokens"]
    if not declared:
        return global_mx
    if not global_mx:
        return declared
    return min(global_mx, declared)


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
    from config import settings
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


# 向 §9 models 层注入产品目录权威成本解析器（含用户自定义模型 / override）。
# 新核心 `observability.cost` 只依赖 `models.pricing`；本模块在应用层加载时接管。
try:
    from models.pricing import register_cost_resolver as _register_cost_resolver

    _register_cost_resolver(resolve_cost)
except Exception:  # noqa: BLE001 - models 层不可用时静默（回退内建目录）
    pass