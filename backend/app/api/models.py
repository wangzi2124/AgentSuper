"""模型目录 API：GET /api/models 动态驱动前端模型选择器 + 模型/Provider 管理。

- `GET /api/models`：目录快照与默认/轻量模型（前端选择器）。
- `GET /api/models/config`：完整配置（目录 + providers + defaults），模型管理面板。
- `POST /api/models/estimate-tokens`：按 DeepSeek V4 官方 tokenizer 估算上下文 token。
- 写操作（custom/providers/defaults）持久化到 data/model_catalog.json 并即时生效，
  模型配置不再依赖后端 .env（保留其作首启动兜底）。
"""

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_admin
from app.api.responses import fail, ok
from app.models import catalog
from app.models.deepseek_tokenizer import count_messages_tokens, count_or_estimate, tokenizer_available

router = APIRouter()


@router.get("/models")
async def list_models():
    """模型目录：{models, default_model, small_model, image_caption_model, voice_model_size}。"""
    return ok({
        "models": catalog.get_catalog(),
        "default_model": catalog.default_model(),
        "small_model": catalog.small_model(),
        "image_caption_model": catalog.image_caption_model(),
        "voice_model_size": catalog.voice_model_size(),
    })


@router.get("/models/config")
async def get_models_config():
    """模型管理面板全量配置：目录条目 + providers + 默认/轻量/图片/语音模型。"""
    return ok({
        "models": catalog.get_catalog(),
        "providers": catalog.provider_models_source(),
        "default_model": catalog.default_model(),
        "small_model": catalog.small_model(),
        "image_caption_model": catalog.image_caption_model(),
        "voice_model_size": catalog.voice_model_size(),
        "source_path": str(catalog.catalog_path()),
    })


@router.post("/models/estimate-tokens")
async def estimate_tokens(req: Request):
    """按官方 tokenizer 估算 token：{text?: str} 或 {messages: [...]}。"""
    body = await req.json()
    method = "deepseek-native" if tokenizer_available() else "chars-estimate"
    text = body.get("text")
    messages = body.get("messages")
    if messages is not None and isinstance(messages, list):
        tokens = count_messages_tokens(messages)
        if tokens <= 0:
            from app.context.token_counter import estimate_tokens_messages as est
            tokens = est(messages)
            method = "estimate-fallback"
        return ok({"tokens": int(tokens), "chars": sum(len(str(m.get('content') or '')) for m in messages), "method": method})
    if not text:
        return fail("text or messages is required")
    tokens = count_or_estimate(text)
    return ok({"tokens": int(tokens), "chars": len(str(text)), "method": method})


@router.post("/models/custom", dependencies=[Depends(require_admin)])
async def upsert_custom(req: Request):
    """新增/更新自定义模型（写 model_catalog.json）。"""
    body = await req.json()
    try:
        entry = catalog.upsert_custom_model(body)
    except ValueError as e:
        return fail(str(e))
    return ok({"model": entry, "models": catalog.get_catalog()})


@router.delete("/models/custom/{mid}", dependencies=[Depends(require_admin)])
async def delete_custom(mid: str):
    removed = catalog.remove_custom_model(mid)
    return ok({"removed": removed, "models": catalog.get_catalog()})


@router.put("/models/providers/{name}", dependencies=[Depends(require_admin)])
async def upsert_prov(name: str, req: Request):
    """新增/更新 provider（api_base 指向 OpenAI 兼容服务，启动探测自动注册其模型）。"""
    body = await req.json()
    entry = catalog.upsert_provider(name, body)
    return ok({"provider": entry, "providers": catalog.provider_models_source(),
               "models": catalog.get_catalog()})


@router.delete("/models/providers/{name}", dependencies=[Depends(require_admin)])
async def delete_prov(name: str):
    removed = catalog.remove_provider(name)
    return ok({"removed": removed, "providers": catalog.provider_models_source(),
               "models": catalog.get_catalog()})


@router.put("/models/defaults", dependencies=[Depends(require_admin)])
async def put_defaults(req: Request):
    """设置默认模型 + 轻量模型 + 图片解析模型 + 语音模型规格。"""
    body = await req.json()
    result = catalog.set_defaults(
        body.get("default_model"),
        body.get("small_model"),
        body.get("image_caption_model"),
        body.get("voice_model_size"),
    )
    return ok({**result, "models": catalog.get_catalog()})


@router.post("/models/reload")
async def reload_models():
    """重新读取 model_catalog.json 覆盖文件 + 重探测（不重启即可生效）。"""
    catalog.reload_catalog()
    return ok({
        "models": catalog.get_catalog(),
        "default_model": catalog.default_model(),
        "small_model": catalog.small_model(),
        "image_caption_model": catalog.image_caption_model(),
        "voice_model_size": catalog.voice_model_size(),
    })