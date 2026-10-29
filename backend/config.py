"""应用配置。

配置来源（从高到低）：
1. ``Settings(...)`` 显式参数（测试）
2. 操作系统环境变量（``export LLM_MODEL=...`` / Docker env / CI）
3. ``backend/config.json``（JSONC：支持 //、/* */ 注释与尾逗号）
4. 字段默认值（本文件）

注意：``.env`` 文件不再读取；需要持久化配置请复制 ``config.example.json`` 为
``config.json`` 后编辑。``config.json`` 已被 gitignore，可安全写入密钥。
"""

import json
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import PydanticBaseSettingsSource


def _strip_jsonc(text: str) -> str:
    """单次扫描：去掉 //、/* */ 注释与对象/数组尾逗号（字符串内不改）。"""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Any:
    """解析 JSONC 文本（标准 JSON + 注释 + 尾逗号）。"""
    return json.loads(_strip_jsonc(text.lstrip("\ufeff")))


class JsonConfigSettingsSource(PydanticBaseSettingsSource):
    """读取 backend/config.json（JSONC）。文件缺失/损坏时返回空字典。"""

    def __init__(self, settings_cls):
        super().__init__(settings_cls)
        self._path = Path(__file__).parent / "config.json"

    def get_field_value(self, field, field_name):
        return None, field_name, False

    def __call__(self):
        if not self._path.exists():
            return {}
        try:
            raw = parse_jsonc(self._path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    """把 JSON 数组或逗号分隔字符串归一为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


class Settings(BaseSettings):
    """应用配置：环境变量 > config.json（JSONC）> 字段默认值。"""

    model_config = SettingsConfigDict(
        extra="allow",
        case_sensitive=False,
        enable_decoding=False,
    )

    # ── LLM 基础连接 ──
    # 请求未指定模型时的默认对话模型（TierModel 的最低优先级兜底）。
    llm_model: str = "deepseek/deepseek-v4-flash"
    # 直连（无代理）时的 API key 兜底；模型目录 Provider 注册的凭证优先。
    llm_api_key: Optional[str] = None
    # 直连端点兜底（litellm base_url）；同样目录 Provider 优先。
    llm_api_base: Optional[str] = "https://api.deepseek.com"

    # ── 持久化后端（session / model_catalog / chapter_store / tasks 的统一后端）──
    # sqlite | mysql | postgresql；sqlite 时各子系统沿用各自 data/*.db。
    db_type: str = "sqlite"
    # 完整 SQLAlchemy URL；非空时优先于下方组件拼装。
    db_url: Optional[str] = None
    db_host: str = "127.0.0.1"
    db_port: int = 0  # 0 = 按类型取默认（mysql 3306 / postgresql 5432）
    db_username: str = "root"
    db_password: str = ""
    db_name: str = "agentsuper"

    # ── 会话与检查点持久化 ──
    # 会话元数据/逻辑删除/压缩基线库路径（相对 backend/）。
    session_db_path: str = "data/session.db"
    # LangGraph 图检查点库路径（相对 backend/）；跨重启保留会话上下文。
    checkpoint_db_path: str = "data/checkpoints.db"
    # false 时退回进程内 MemorySaver（重启即丢，仅调试用）。
    checkpoint_persist: bool = True
    # 可选 PostgresSaver 连接串（需 psycopg）；为空则用 SQLite。
    postgres_checkpoint_url: str = ""
    # Postgres checkpointer 的通用兜底连接串（POSTGRES_CHECKPOINT_URL 为空时读取）。
    database_url: str = ""

    # ── 向量库 / 文档入库 ──
    # ChromaDB 持久化目录（相对 backend/）。
    vector_store_path: str = "data/vector_store"
    chunk_size: int = 500
    chunk_overlap: int = 200
    # 强制 metadata 字段 department（部门/租户，权限过滤用）。
    kb_department: str = "default"
    # 本地 embedding 模型（ModelScope 优先，HF 兜底）。
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    # 重排序开关与模型；失败时自动降级为不重排。
    enable_reranker: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # 模型下载容错：单次 snapshot_download 超时（秒，0 = 不设整体超时）。
    model_download_timeout: int = 600
    # 单个下载源内的重试次数（不含首次）。
    model_download_retries: int = 2

    # 插件发现目录（相对 backend/）。
    plugins_dir: str = "plugins"

    # ── 历史摘要 / 压缩模型 ──
    # 历史摘要模型；compress_model 为空时作为压缩模型回退。
    summarization_model: Optional[str] = None
    # 压缩时保留的最近消息条数（每次压缩实时读取，支持 API 热更新）。
    summarization_keep_messages: int = 6
    # 压缩专用模型；优先于 summarization_model，空 = tier-simple。
    compress_model: Optional[str] = None
    # 是否启用 LLM 摘要压缩（false = 仅规则截断）。
    compress_model_enabled: bool = True
    # 单次摘要输出硬上限。
    compress_max_tokens: int = 800

    # ── 计划模型 / 多 Agent 扇出 ──
    plan_model_enabled: bool = True
    # 并行扇出默认关闭；开启后仍需通过预算守卫。
    plan_parallel_enabled: bool = False
    # 单会话并行扇出次数上限。
    plan_max_fanout_calls: int = 2
    # 扇出前要求的本会话剩余预算（¥）。
    plan_fanout_min_budget: float = 0.20
    # 本会话总预算（¥）；0 = 不设预算（扇出门禁按未知预算保守拒绝）。
    session_budget: float = 0.0

    # ── 语音 / TTS（本地 Qwen3-TTS 子进程）──
    voice_tts_enabled: bool = False
    voice_tts_speaker: str = "Vivian"
    voice_tts_model_size: str = "0.6B"
    voice_tts_timeout: int = 600

    # ── 输出 token ──
    # 每次 LLM 调用的输出上限；实际取 min(模型目录声明, 本值)。
    llm_max_tokens: int = 32_000
    # token 估算校正系数（cognition/token_counter.py）。
    token_estimate_correction: float = 1.13

    # ── 上下文压缩 ──
    # 压缩时尾部保留的最近轮次。
    context_tail_turns: int = 2
    # 自动压缩保留的最近消息条数（0 = 按 context_tail_turns × 2 折算）。
    compaction_tail_messages: int = 0
    # 单条工具输出 token 上限（bound_tool_output 使用）。
    tool_output_max_tokens: int = 8_000

    # ── 图片 / OCR / 视觉描述桥 ──
    image_thumbnail_px: int = 256
    image_use_ocr: bool = False
    tesseract_cmd: str = ""
    tessdata_dir: str = ""
    ocr_languages: str = "chi_sim+eng"
    image_vlm_caption: bool = True
    image_caption_model: str = "openai/gpt-4o-mini"
    image_caption_api_base: str = ""
    image_caption_api_key: str = ""
    image_caption_prompt: str = ""
    image_caption_timeout: int = 15

    # ── 管理端 / CORS ──
    # 设置后管理接口要求 Authorization: Bearer <token>；为空时仅本机可管理。
    admin_token: Optional[str] = None
    # 允许的前端来源（JSON 数组或逗号分隔字符串）。
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]

    # ── 知识库清理 ──
    vector_store_auto_clear: bool = False
    vector_store_ttl_days: int = 0
    vector_store_cleanup_interval_hours: int = 24

    # ── 并发 ──
    max_concurrent_agents: int = 4
    # 超出并发上限后的排队长度上限。
    max_queue_size: int = 20
    # 工具默认限流（0 = 不限）：仅对未显式声明 rate_limit / token_budget 的工具生效。
    tool_default_rate_limit: int = 0
    tool_default_token_budget: int = 0

    # ── 权限 / 工作区 ──
    permission_approval_timeout: int = 180
    allow_source_writes: bool = False

    # ── 思考模式 / Ollama ──
    deepseek_thinking: str = "disabled"
    ollama_thinking: str = "disabled"
    # 读不到运行窗口时的回落值（实际以 Ollama /api/ps 为准）。
    ollama_num_ctx: int = 4096
    ollama_context_guard: bool = True
    # Ollama 服务地址（目录未注册时兜底）。
    ollama_host: str = "http://localhost:11434"
    # 显式 Ollama API base（优先级高于 ollama_host；为空则用 ollama_host）。
    ollama_api_base: str = ""

    # ── LiteLLM / 可观测性 ──
    litellm_debug: bool = True
    # LiteLLM 代理模式地址与 key；为空则直连 Provider。
    litellm_proxy_url: str = ""
    litellm_proxy_key: str = "sk-litellm-proxy"
    # LiteLLM debug 日志路径（相对 backend/）。
    litellm_debug_log: str = "data/logs/litellm-debug.log"
    # token trace 等运行日志目录。
    agentsuper_log_dir: str = "logs"
    # 全局存储目录覆盖（空 = backend/ 下的默认位置）。
    agentsuper_data: str = ""
    agentsuper_cache: str = ""
    agentsuper_config: str = ""
    agentsuper_state: str = ""
    agentsuper_log: str = ""
    agentsuper_bin: str = ""
    # 自定义 Provider 模型探测超时（秒）。
    model_probe_timeout: float = 4.0
    # DeepSeek tokenizer 目录（为空则用 data/models 或仓库内置）。
    deepseek_tokenizer_dir: str = ""
    # Langfuse 链路追踪；非空时启用，并导出到进程环境供 litellm/langfuse 读取。
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # ── 共享记忆 ──
    memory_persist_path: str = "data/agent_memory.db"

    # ── 用户身份签名（可选）──
    # 设置后启用登录；字段同时接受环境变量 AUTH_TOKEN_SECRET。
    auth_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AUTH_TOKEN_SECRET", "auth_secret"),
    )
    auth_token_ttl: int = 2592000
    auth_users_path: str = "data/auth_users.json"

    # ── 全链路日志（chainlog）──
    chain_log_enabled: bool = True
    chain_log_queue_size: int = 5000
    chain_log_flush_interval: float = 1.0
    chain_log_flush_batch: int = 200
    chain_log_retention_days: int = 7
    chain_log_max_rows: int = 200000
    chain_log_max_data_chars: int = 4000
    chain_log_tool_calls: bool = True

    # ── 联网工具 / SSRF ──
    tavily_api_key: str = ""
    bing_api_key: str = ""
    # true 时放行内网地址（仅本地调试；协议/格式校验仍生效）。
    ssrf_allow_internal: bool = False

    # ── 跨模型工具调用 Harness ──
    # 全局强制工具调用路径：native | fallback；空 = 按模型目录能力探测。
    harness_tool_mode: str = ""
    # 额外声明原生支持 / 不支持原生的模型（JSON 数组或逗号分隔）。
    harness_native_models: list[str] = []
    harness_fallback_models: list[str] = []

    # ── 真实模型验收测试（pytest 专用，默认关闭）──
    harness_real_test: bool = False
    harness_real_model: str = ""
    l4_model_switch: bool = False
    l4_model_a: str = ""
    l4_model_b: str = ""
    # 分号分隔的 A:B 模型对（设置后覆盖 l4_model_a/b）。
    l4_model_pairs: str = ""
    harness_smoke_model_a: str = ""
    harness_smoke_model_b: str = ""
    harness_ollama_test: bool = False
    harness_ollama_tools_model: str = "ollama/qwen2.5:3b"
    harness_ollama_text_model: str = "ollama/deepseek-r1:latest"

    @field_validator("cors_origins", "harness_native_models", "harness_fallback_models", mode="before")
    @classmethod
    def _coerce_str_list(cls, value: Any) -> list[str]:
        return _as_str_list(value)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # 优先级：显式参数 > 系统环境变量 > config.json > secrets。
        # 不再注册 dotenv_settings：.env 文件不再读取。
        return (
            init_settings,
            env_settings,
            JsonConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


settings = Settings()
