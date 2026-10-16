"""运维/热更新接口（对齐 agent.md §9 app/api/admin.py）。

- `GET /admin/tools`：工具注册表快照（scope / 是否需审批）
- `GET /admin/agents`：子 Agent 规格
- `GET /admin/prompts`：提示词清单 + 内容哈希（§7.7.3 归因）
- `GET /admin/metrics`：Token/成本计量快照（§11.3）
- `GET /admin/approvals` / `POST /admin/approvals/{id}`：高危工具审批
- `GET /admin/health`：健康检查
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from prompts.loader import PROMPTS_DIR, prompt_hash

from ..deps import PlatformContext, get_context

router = APIRouter(prefix="/admin", tags=["admin"])


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = ""


@router.get("/tools")
async def list_tools(ctx: PlatformContext = Depends(get_context)) -> dict:
    tools = [
        {
            "name": td.name,
            "scope": td.scope,
            "require_approval": td.require_approval,
            "description": (td.description or "").strip(),
        }
        for td in ctx.registry.definitions(None)
    ]
    return {"tools": tools, "count": len(tools)}


@router.get("/agents")
async def list_agents(ctx: PlatformContext = Depends(get_context)) -> dict:
    return {
        "agents": [
            {
                "name": spec.name,
                "tier": spec.tier,
                "tools": list(spec.tools),
                "description": spec.description,
            }
            for spec in ctx.subagents.values()
        ]
    }


def _prompt_files() -> list[str]:
    base = PROMPTS_DIR
    files: list[str] = []
    for sub in ("providers", "system", "nodes"):
        d = base / sub
        if d.is_dir():
            files.extend(f"{sub}/{p.name}" for p in sorted(d.glob("*.md")))
    return files


@router.get("/prompts")
async def list_prompts() -> dict:
    items = []
    for rel in _prompt_files():
        entry: dict = {"path": rel}
        try:
            agent_type = rel.split("/", 1)[1].removesuffix(".md")
            entry["hash"] = prompt_hash(agent_type)
        except Exception:  # noqa: BLE001 - provider/node 模板无哈希
            entry["hash"] = None
        items.append(entry)
    return {"prompts": items, "count": len(items)}


@router.get("/metrics")
async def metrics(ctx: PlatformContext = Depends(get_context)) -> dict:
    return ctx.metrics.snapshot()


@router.get("/approvals")
async def list_approvals(
    status: str = "pending", ctx: PlatformContext = Depends(get_context)
) -> dict:
    items = await ctx.approvals.list(status or None)
    return {
        "approvals": [
            {
                "id": r.id,
                "tool": r.tool,
                "args": r.args,
                "status": r.status,
                "reason": r.reason,
            }
            for r in items
        ]
    }


@router.post("/approvals/{req_id}")
async def decide_approval(
    req_id: str, body: ApprovalDecision, ctx: PlatformContext = Depends(get_context)
) -> dict:
    ok = await ctx.approvals.decide(req_id, body.approved, body.reason)
    if not ok:
        raise HTTPException(status_code=404, detail=f"审批请求不存在: {req_id}")
    return {"id": req_id, "approved": body.approved}


@router.get("/health")
async def health(ctx: PlatformContext = Depends(get_context)) -> dict:
    return {
        "status": "ok",
        "tools": len(ctx.registry.names()),
        "agents": list(ctx.subagents),
        "sessions": len(ctx.sessions.list()),
    }
