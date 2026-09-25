"""技能管理 API 路由模块。

提供技能列表查询、启用/禁用切换，以及技能目录设置（目录由前端在
「自定义工具」页选择，持久化于 data/runtime_skills_dir.json）。
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

# 技能目录持久化文件（独立于 .env，前端选择后重启仍生效）
RUNTIME_SKILLS_DIR_FILE = "data/runtime_skills_dir.json"


class ToggleSkillRequest(BaseModel):
    """技能切换请求模型。"""
    enabled: bool


class SetSkillsDirRequest(BaseModel):
    """设置技能目录请求模型。"""
    directory: str


def _current_skills_dir() -> str:
    """读取当前技能目录（未设置时回退到 backend/skills）。"""
    p = Path(__file__).resolve().parents[2] / RUNTIME_SKILLS_DIR_FILE  # backend/runtime_skills_dir.json
    try:
        if p.exists():
            data = json.loads(p.read_text("utf-8"))
            d = str(data.get("directory", "")).strip()
            if d:
                return d
    except Exception as e:
        logger.warning("Failed to read skills dir: %s", e)
    return str(Path(__file__).resolve().parents[1] / "skills")


def _save_skills_dir(directory: str) -> None:
    """持久化技能目录。"""
    p = Path(__file__).resolve().parents[2] / RUNTIME_SKILLS_DIR_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"directory": directory}, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save skills dir: %s", e)


@router.get("/")
async def list_skills(request: Request):
    """获取所有可用技能的列表。"""
    loader = getattr(request.app.state, "skill_loader", None)
    if loader is None:
        return []
    return [s.to_dict() for s in loader.list()]


@router.post("/{name}/toggle")
async def toggle_skill(name: str, body: ToggleSkillRequest, request: Request):
    """启用或禁用指定技能。"""
    require_admin(request)
    loader = getattr(request.app.state, "skill_loader", None)
    if loader is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    if not loader.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail="Skill not found")
    loader.load_all()
    agent = getattr(request.app.state, "agent", None)
    if agent is not None and hasattr(agent, "refresh_tools"):
        await agent.refresh_tools()
    return {"message": f"Skill '{name}' {'enabled' if body.enabled else 'disabled'}"}


@router.get("/directory")
async def get_skills_directory(request: Request):
    """返回当前技能目录路径。"""
    loader = getattr(request.app.state, "skill_loader", None)
    cur = str(loader.skills_dir) if loader is not None else _current_skills_dir()
    return {"directory": cur}


@router.post("/directory")
async def set_skills_directory(body: SetSkillsDirRequest, request: Request):
    """设置技能目录：切换 SkillLoader 扫描目录并热加载（前端「自定义工具」页选择）。"""
    require_admin(request)
    directory = str(body.directory).strip()
    if not directory:
        raise HTTPException(status_code=400, detail="directory 不能为空")
    d = Path(directory).expanduser().resolve()
    if not d.is_dir():
        raise HTTPException(status_code=400, detail=f"技能目录不存在: {d}")
    loader = getattr(request.app.state, "skill_loader", None)
    if loader is None:
        from app.skills.loader import SkillLoader
        loader = SkillLoader(str(d), create=False)
        request.app.state.skill_loader = loader
    else:
        try:
            loader.set_dir(str(d))
        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e))
    _save_skills_dir(str(d))
    agent = getattr(request.app.state, "agent", None)
    if agent is not None and hasattr(agent, "refresh_tools"):
        await agent.refresh_tools()
    return {"directory": str(d), "skills": [s.to_dict() for s in loader.list()]}