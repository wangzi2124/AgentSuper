# -*- coding: utf-8 -*-
"""自定义工具 API。

前端「自定义工具」页面的后端接口：
  - 粘贴 Python 源码（含 tool_* 函数）→ 写入 plugins/custom_*.py → 热加载
所有写操作后都会 reload 插件并 sync_plugin_tools。
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


class ScriptRequest(BaseModel):
    name: str
    description: str = ""
    script: str
    enabled: bool = True


class ToggleRequest(BaseModel):
    enabled: bool


def _store(request: Request):
    store = getattr(request.app.state, "custom_tools", None)
    if store is None:
        raise HTTPException(status_code=500, detail="custom_tools store not initialized")
    return store


async def _reload(request: Request) -> None:
    """热加载：重新扫描脚本 → 同步进工具表（新增/禁用/删除立即对模型生效）。"""
    loader = getattr(request.app.state, "plugin_loader", None)
    try:
        if loader is not None:
            loader.load_all()
    except Exception as e:  # noqa: BLE001
        logger.warning("plugin reload failed: %s", e)
    platform = getattr(request.app.state, "platform", None)
    registry = getattr(platform, "registry", None)
    if registry is not None and loader is not None:
        try:
            from extensions.plugins import sync_plugin_tools

            sync_plugin_tools(registry, loader)
        except Exception as e:  # noqa: BLE001
            logger.warning("sync_plugin_tools failed: %s", e)


@router.get("/")
async def list_custom_tools(request: Request):
    """列出所有自定义工具（脚本型）。"""
    return _store(request).list()


@router.post("/script")
async def create_script(body: ScriptRequest, request: Request):
    """创建脚本型自定义工具（写入 plugins/custom_*.py 并热加载）。"""
    require_admin(request)
    store = _store(request)
    try:
        item = store.create_script(body.name.strip(), body.description.strip(), body.script, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload(request)
    return item


@router.post("/{name}/toggle")
async def toggle_custom(name: str, body: ToggleRequest, request: Request):
    """启用/禁用自定义工具（改 .enabled 文件）。"""
    require_admin(request)
    store = _store(request)
    if not store.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"Custom tool not found: {name}")
    await _reload(request)
    return {"message": f"Custom tool '{name}' {'enabled' if body.enabled else 'disabled'}"}


@router.delete("/{name}")
async def delete_custom(name: str, request: Request):
    """删除自定义工具（删脚本与 .enabled 文件）。"""
    require_admin(request)
    store = _store(request)
    if not store.remove(name):
        raise HTTPException(status_code=404, detail=f"Custom tool not found: {name}")
    await _reload(request)
    return {"message": f"Custom tool '{name}' deleted"}
