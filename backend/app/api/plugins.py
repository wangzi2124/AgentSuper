from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class TogglePluginRequest(BaseModel):
    enabled: bool


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
