from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Dict

router = APIRouter()


class TogglePluginRequest(BaseModel):
    enabled: bool


class CallPluginRequest(BaseModel):
    args: Dict[str, Any] = {}


@router.get("/")
async def list_plugins(request: Request):
    loader = request.app.state.plugin_loader
    return [p.to_dict() for p in loader.list()]


@router.post("/{name}/toggle")
async def toggle_plugin(name: str, body: TogglePluginRequest, request: Request):
    loader = request.app.state.plugin_loader
    if not loader.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail="Plugin not found")
    loader.load_all()
    request.app.state.agent.refresh_tools()
    return {"message": f"Plugin '{name}' {'enabled' if body.enabled else 'disabled'}"}


@router.get("/{name}/status")
async def get_plugin_status(name: str, request: Request):
    loader = request.app.state.plugin_loader
    for plugin in loader.list():
        if plugin.name == name:
            return {"enabled": plugin.enabled}
    raise HTTPException(status_code=404, detail="Plugin not found")


@router.post("/{name}/call/{function_name}")
async def call_plugin_function(name: str, function_name: str, body: CallPluginRequest, request: Request):
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
