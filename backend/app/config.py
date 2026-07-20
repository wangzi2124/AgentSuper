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


settings = Settings()
