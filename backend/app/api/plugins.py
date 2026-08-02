"""插件管理 API 路由模块。

提供插件列表查询、启用/禁用切换、状态查询和函数调用功能。
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Dict

from app.api.deps import require_admin

router = APIRouter()

# 允许通过 HTTP 直接调用的插件白名单（只读/生成类，安全）。
# 其余插件（http-client、file_reader、filesystem 等）只能经由 Agent 调用，
# 以保证权限系统（PermissionManager）的审批流程不被绕过。
_HTTP_CALLABLE_PLUGINS = frozenset({
    "example-plugin", "weather-alert", "internet-search",
    "docx-generator", "pdf-generator", "excel-generator", "pptx-generator", "kb-export",
})


class TogglePluginRequest(BaseModel):
    """插件切换请求模型。"""
    enabled: bool


class CallPluginRequest(BaseModel):
    """插件函数调用请求模型。"""
    args: Dict[str, Any] = {}


@router.get("/")
async def list_plugins(request: Request):
    """获取所有可用插件的列表。"""
    loader = request.app.state.plugin_loader
    return [p.to_dict() for p in loader.list()]


@router.post("/{name}/toggle")
async def toggle_plugin(name: str, body: TogglePluginRequest, request: Request):
    """启用或禁用指定插件。"""
    require_admin(request)
    loader = request.app.state.plugin_loader
    if not loader.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail="Plugin not found")
    loader.load_all()
    request.app.state.agent.refresh_tools()
    return {"message": f"Plugin '{name}' {'enabled' if body.enabled else 'disabled'}"}


@router.get("/{name}/status")
async def get_plugin_status(name: str, request: Request):
    """获取指定插件的启用状态。"""
    loader = request.app.state.plugin_loader
    for plugin in loader.list():
        if plugin.name == name:
            return {"enabled": plugin.enabled}
    raise HTTPException(status_code=404, detail="Plugin not found")


@router.post("/{name}/call/{function_name}")
async def call_plugin_function(name: str, function_name: str, body: CallPluginRequest, request: Request):
    """调用指定插件的函数并返回结果。"""
    if name not in _HTTP_CALLABLE_PLUGINS:
        raise HTTPException(
            status_code=403,
            detail=f"Plugin '{name}' cannot be invoked over HTTP; it must go through the Agent"
        )
    require_admin(request)
    loader = request.app.state.plugin_loader
    try:
        result = loader.call_function(name, function_name, **body.args)
        if isinstance(result, str):
            try:
                import json
                return json.loads(result)
            except json.JSONDecodeError:
                return {"result": result}
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
