"""技能管理 API 路由模块。

提供技能列表查询和启用/禁用切换功能。
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin

router = APIRouter()


class ToggleSkillRequest(BaseModel):
    """技能切换请求模型。"""
    enabled: bool


@router.get("/")
async def list_skills(request: Request):
    """获取所有可用技能的列表。"""
    loader = request.app.state.skill_loader
    return [s.to_dict() for s in loader.list()]


@router.post("/{name}/toggle")
async def toggle_skill(name: str, body: ToggleSkillRequest, request: Request):
    """启用或禁用指定技能。"""
    require_admin(request)
    loader = request.app.state.skill_loader
    if not loader.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail="Skill not found")
    loader.load_all()
    request.app.state.agent.refresh_tools()
    return {"message": f"Skill '{name}' {'enabled' if body.enabled else 'disabled'}"}
