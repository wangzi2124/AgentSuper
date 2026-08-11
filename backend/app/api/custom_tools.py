# -*- coding: utf-8 -*-
"""自定义工具 API [token 优化 v6]。

前端「Skills → 自定义工具」页面的后端接口：
  - 脚本型：粘贴 Python 源码（含 tool_* 函数）→ 写入 plugins/custom_*.py → 热加载
  - 固定型：把已有工具 pin 到常驻列表（按需挂载时始终挂载其 schema）
所有写操作后都会 reload 插件并 refresh_tools（与 skills toggle 同一链路）。
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


class PinRequest(BaseModel):
    tool_name: str
    description: str = ""


class ToggleRequest(BaseModel):
    enabled: bool


def _store(request: Request):
    store = getattr(request.app.state, "custom_tools", None)
    if store is None:
        raise HTTPException(status_code=500, detail="custom_tools store not initialized")
    return store


async def _reload(request: Request) -> None:
    """热加载：重新扫描插件 + 刷新 agent 工具（与 skills toggle 相同链路）。"""
    try:
        request.app.state.plugin_loader.load_all()
    except Exception as e:  # noqa: BLE001
        logger.warning("plugin reload failed: %s", e)
    try:
        await request.app.state.agent.refresh_tools()
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh_tools failed: %s", e)


@router.get("/")
async def list_custom_tools(request: Request):
    """列出所有自定义工具（脚本型 + 固定型）。"""
    return _store(request).list()


@router.get("/catalog")
async def tool_catalog(request: Request):
    """返回当前所有可用工具目录（供前端「固定已有工具」下拉选择）。"""
    names: list[dict] = []
    agent = getattr(request.app.state, "agent", None)
    if agent is not None:
        for t in getattr(agent, "tools", []):
            names.append({
                "name": t.name,
                "description": (getattr(t, "description", "") or "")[:120],
            })
    return names


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


@router.post("/pin")
async def create_pin(body: PinRequest, request: Request):
    """固定一个已有工具：按需挂载时始终保留其 schema。"""
    require_admin(request)
    store = _store(request)
    catalog = {t["name"] for t in await tool_catalog(request)}
    if body.tool_name.strip() not in catalog:
        raise HTTPException(status_code=400, detail=f"工具不存在: {body.tool_name}")
    try:
        pin = store.create_pin(body.tool_name.strip(), body.description.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload(request)
    return pin


@router.post("/{name}/toggle")
async def toggle_custom(name: str, body: ToggleRequest, request: Request):
    """启用/禁用自定义工具（脚本型改 .enabled 文件，固定型改 pins 列表）。"""
    require_admin(request)
    store = _store(request)
    if not store.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"Custom tool not found: {name}")
    await _reload(request)
    return {"message": f"Custom tool '{name}' {'enabled' if body.enabled else 'disabled'}"}


@router.delete("/{name}")
async def delete_custom(name: str, request: Request):
    """删除自定义工具（脚本型删文件，固定型删 pin 条目）。"""
    require_admin(request)
    store = _store(request)
    if not store.remove(name):
        raise HTTPException(status_code=404, detail=f"Custom tool not found: {name}")
    await _reload(request)
    return {"message": f"Custom tool '{name}' deleted"}
