"""技能管理 API 路由模块。

提供技能列表/详情查询、受管库内技能的增删改、外部技能源的追加与移除。
存储模型见 `app/skills/registry.py`：受管库 `data/skills`（自动创建，UI 新建的技能
落这里）+ 若干外部源（`data/skills_sources.json` 持久化），同名以受管库优先。
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.skills import registry

logger = logging.getLogger(__name__)
router = APIRouter()


class ToggleSkillRequest(BaseModel):
    """技能切换请求模型。"""
    enabled: bool


class SkillBody(BaseModel):
    """新建技能请求模型。"""
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""
    content: str = ""
    disable_model_invocation: bool = False
    enabled: bool = True


class SkillUpdate(BaseModel):
    """编辑技能请求模型（未传字段保持原样）。"""
    description: str | None = None
    content: str | None = None
    disable_model_invocation: bool | None = None
    enabled: bool | None = None


class SetSkillsDirRequest(BaseModel):
    """追加外部技能源请求模型。"""
    directory: str


class RemoveSkillsDirRequest(BaseModel):
    """移除外部技能源请求模型。"""
    directory: str


def _loader(request: Request):
    """取当前 SkillLoader；缺失时按注册表重建一个（首次访问的兜底）。"""
    loader = getattr(request.app.state, "skill_loader", None)
    if loader is None:
        from app.skills.loader import SkillLoader
        managed, extras = registry.load_sources()
        loader = SkillLoader(managed, create=True, extra_dirs=extras)
        loader.load_all()
        request.app.state.skill_loader = loader
    return loader


async def _refresh(request: Request, loader) -> None:
    """热重建 LangGraph 工具表（新增/编辑/删除/启停后都要调）。"""
    registry.save_sources(str(loader.skills_dir), loader.extra_dir_strings())
    agent = getattr(request.app.state, "agent", None)
    if agent is not None and hasattr(agent, "refresh_tools"):
        await agent.refresh_tools()


def _sources_payload(loader) -> dict:
    return {
        "managed": str(loader.skills_dir),
        "directory": str(loader.skills_dir),  # 兼容旧前端字段
        "extra_dirs": loader.extra_dir_strings(),
    }


# ── 列表 / 详情 ────────────────────────────────────────────────────────

@router.get("/")
async def list_skills(request: Request):
    """获取所有可用技能的列表（含来源标记）。"""
    loader = _loader(request)
    out = []
    for s in loader.list():
        d = s.to_dict()
        d["source"] = loader.source_of(s.name)
        out.append(d)
    return out


@router.get("/directory")
async def get_skills_directory(request: Request):
    """返回受管库路径与已追加的外部源列表。"""
    return _sources_payload(_loader(request))


@router.get("/{name}")
async def get_skill(name: str, request: Request):
    """返回单个技能的元信息 + 完整正文（供编辑表单回填）。"""
    loader = _loader(request)
    skill = loader.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"技能不存在: {name}")
    d = skill.to_dict()
    d["source"] = loader.source_of(name)
    d["content"] = loader.get_skill_content(name) or ""
    return d


# ── 外部技能源 ─────────────────────────────────────────────────────────

@router.post("/directory")
async def add_skills_directory(body: SetSkillsDirRequest, request: Request):
    """**追加**一个外部技能源（不替换受管库，自建技能不会因此消失）。"""
    require_admin(request)
    directory = str(body.directory).strip()
    if not directory:
        raise HTTPException(status_code=400, detail="directory 不能为空")
    d = Path(directory).expanduser().resolve()
    if not d.is_dir():
        raise HTTPException(status_code=400, detail=f"技能目录不存在: {d}")
    loader = _loader(request)
    try:
        skills = loader.add_dir(str(d))
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _refresh(request, loader)
    return {**_sources_payload(loader), "skills": [s.to_dict() for s in skills]}


@router.post("/directory/remove")
async def remove_skills_directory(body: RemoveSkillsDirRequest, request: Request):
    """移除一个外部技能源（受管库不可移除）。"""
    require_admin(request)
    directory = str(body.directory).strip()
    if not directory:
        raise HTTPException(status_code=400, detail="directory 不能为空")
    loader = _loader(request)
    skills = loader.remove_dir(directory)
    await _refresh(request, loader)
    return {**_sources_payload(loader), "skills": [s.to_dict() for s in skills]}


# ── 受管库 CRUD ────────────────────────────────────────────────────────

@router.post("/")
async def create_skill(body: SkillBody, request: Request):
    """在受管库新建技能（写 `data/skills/<name>/SKILL.md`）。"""
    require_admin(request)
    loader = _loader(request)
    try:
        skill = loader.create_skill(
            body.name, body.description, body.content,
            disable_model_invocation=body.disable_model_invocation,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _refresh(request, loader)
    return skill.to_dict()


@router.put("/{name}")
async def update_skill(name: str, body: SkillUpdate, request: Request):
    """编辑受管库中的技能（外部源的技能返回 403）。"""
    require_admin(request)
    loader = _loader(request)
    try:
        skill = loader.update_skill(
            name,
            description=body.description,
            content=body.content,
            disable_model_invocation=body.disable_model_invocation,
            enabled=body.enabled,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e).strip("'"))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _refresh(request, loader)
    return skill.to_dict()


@router.delete("/{name}")
async def delete_skill(name: str, request: Request):
    """删除受管库中的技能（外部源的技能一律 403）。"""
    require_admin(request)
    loader = _loader(request)
    if loader.get(name) is None:
        raise HTTPException(status_code=404, detail=f"技能不存在: {name}")
    if not loader.delete_skill(name):
        raise HTTPException(status_code=403, detail="只能删除受管技能库中的技能")
    await _refresh(request, loader)
    return {"message": f"Skill '{name}' deleted"}


@router.post("/{name}/toggle")
async def toggle_skill(name: str, body: ToggleSkillRequest, request: Request):
    """启用或禁用指定技能。"""
    require_admin(request)
    loader = _loader(request)
    if not loader.toggle(name, body.enabled):
        raise HTTPException(status_code=404, detail=f"技能不存在: {name}")
    loader.load_all()
    await _refresh(request, loader)
    return {"message": f"Skill '{name}' {'enabled' if body.enabled else 'disabled'}"}
