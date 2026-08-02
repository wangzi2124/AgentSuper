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

    # Token 成本控制
    # 每次 LLM 调用允许的最大上下文（system + history + 当前问题）
    max_context_tokens: int = 24_000
    # 单次请求内最大工具调用轮数（每轮都是一次完整 LLM 调用）
    # 可用环境变量 MAX_TOOL_ROUNDS 覆盖
    max_tool_rounds: int = 24
    # 摘要中间件缓存大小（按历史分块缓存，避免每请求全量重算）
    summarization_cache_size: int = 200

    # 可选：管理端鉴权 token。设置后插件 toggle/call、权限审批等敏感接口需携带
    # Authorization: Bearer <token>；不设置则保持本地单用户模式（不做校验）。
    admin_token: Optional[str] = None


settings = Settings()
