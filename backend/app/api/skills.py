from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class ToggleSkillRequest(BaseModel):
    enabled: bool


@router.get("/")
async def list_skills(request: Request):
    loader = request.app.state.skill_loader
    return [s.to_dict() for s in loader.list()]


@router.post("/{name}/toggle")
async def toggle_skill(name: str, body: ToggleSkillRequest, request: Request):
    loader = request.app.state.skill_loader
    if not loader.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail="Skill not found")
    loader.load_all()
    request.app.state.agent.refresh_tools()
    return {"message": f"Skill '{name}' {'enabled' if body.enabled else 'disabled'}"}
