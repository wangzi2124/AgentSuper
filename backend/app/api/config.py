"""配置管理 API 路由模块。

提供摘要功能的配置查询和更新接口。
"""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.config import settings
from app.api.chat import reset_summarizer

logger = logging.getLogger(__name__)

router = APIRouter()


class SummarizationConfig(BaseModel):
    """摘要配置更新请求模型。"""
    model: Optional[str] = None
    keep_messages: Optional[int] = None


class SummarizationStatus(BaseModel):
    """摘要配置状态响应模型。"""
    model: Optional[str]
    keep_messages: int
    enabled: bool


@router.get("/summarization", response_model=SummarizationStatus)
async def get_summarization_config():
    """获取当前摘要功能的配置状态。"""
    return SummarizationStatus(
        model=settings.summarization_model,
        keep_messages=settings.summarization_keep_messages,
        enabled=settings.summarization_model is not None,
    )


@router.post("/summarization", response_model=SummarizationStatus)
async def update_summarization_config(body: SummarizationConfig):
    """更新摘要功能的配置参数。"""
    if body.model is not None:
        settings.summarization_model = body.model if body.model else None
    if body.keep_messages is not None:
        settings.summarization_keep_messages = max(1, body.keep_messages)

    reset_summarizer()

    logger.info(
        "Summarization config updated: model=%s, keep=%d",
        settings.summarization_model,
        settings.summarization_keep_messages,
    )

    return SummarizationStatus(
        model=settings.summarization_model,
        keep_messages=settings.summarization_keep_messages,
        enabled=settings.summarization_model is not None,
    )
