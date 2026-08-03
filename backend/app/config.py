from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """应用配置，从 .env 文件读取环境变量。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    llm_model: str = "deepseek-chat"
    llm_api_key: Optional[str] = None
    llm_api_base: Optional[str] = "https://api.deepseek.com"

    vector_store_path: str = "data/vector_store"
    upload_dir: str = "data/uploads"

    chunk_size: int = 500
    chunk_overlap: int = 200

    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    enable_reranker: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    skills_dir: str = "skills"
    plugins_dir: str = "plugins"

    summarization_model: Optional[str] = None
    summarization_api_key: Optional[str] = None
    summarization_api_base: Optional[str] = None
    summarization_keep_messages: int = 20

    # 多 Agent 超时（秒）
    # supervisor 转发到子 Agent 的等待上限（单次生成可能因 LLM 延迟/tool 循环超 60s）
    sub_agent_timeout: float = 150.0
    # 端点层等待 supervisor 返回的上限（需 > decompose + sub_agent + synthesize）
    supervisor_timeout: float = 300.0

    # Token 成本控制
    # 每次 LLM 调用允许的最大上下文（system + history + 当前问题）
    # 对齐 opencode overflow.ts：usable = max_context_tokens - context_reserve_tokens
    max_context_tokens: int = 64_000
    # 输出预留：留给模型回答的 token（≈ min(20_000, maxOutputTokens)，默认 8_192）
    context_reserve_tokens: int = 8_192
    # 压缩触发阈值（token）；0 表示自动 = 0.8 × usable，长工具循环在截断兜底之前先压缩
    compaction_threshold_tokens: int = 0
    # 压缩时尾部保留的最近轮次（对齐 opencode tail_turns，默认 2）
    context_tail_turns: int = 2
    # 尾部保留的 token 预算（对齐 opencode preserve_recent_tokens，默认 8_000）
    context_preserve_recent_tokens: int = 8_000
    # 回溯式工具输出清理：最近 N 轮之内累计工具输出超过该值时，清理更旧输出（默认 40_000）
    tool_output_protect_tokens: int = 40_000
    # 清理收益低于该值时不做（避免微小收益的频繁改写）
    tool_output_prune_minimum_tokens: int = 20_000
    # 单次请求内最大工具调用轮数（每轮都是一次完整 LLM 调用）
    # 可用环境变量 MAX_TOOL_ROUNDS 覆盖
    max_tool_rounds: int = 24
    # 摘要中间件缓存大小（按历史分块缓存，避免每请求全量重算）
    summarization_cache_size: int = 200

    # 可选：管理端鉴权 token。设置后插件 toggle/call、权限审批等敏感接口需携带
    # Authorization: Bearer <token>；不设置则保持本地单用户模式（不做校验）。
    admin_token: Optional[str] = None


settings = Settings()
