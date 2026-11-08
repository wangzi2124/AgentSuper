"""Read-only policy inspection API (opencode gap G-06)."""

from __future__ import annotations

from fastapi import APIRouter

from policy.engine import ACTIONS, PolicyEngine

router = APIRouter(prefix="/v2", tags=["Policies"])


@router.get("/policies")
async def list_policies() -> dict:
    engine = PolicyEngine.from_settings()
    return {
        "actions": sorted(ACTIONS),
        "statements": [
            {
                "action": item.action,
                "resource": item.resource,
                "effect": item.effect,
                "source": item.source,
            }
            for item in engine.statements()
        ],
    }


@router.post("/policies/explain")
async def explain_policy(payload: dict) -> dict:
    action = str(payload.get("action") or "").strip().lower()
    resource = str(payload.get("resource") or "").strip()
    if not action:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="action is required")
    decision = PolicyEngine.from_settings().evaluate(action, resource)
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "action": decision.action,
        "resource": decision.resource,
        "matched": (
            {
                "action": decision.matched.action,
                "resource": decision.matched.resource,
                "effect": decision.matched.effect,
                "source": decision.matched.source,
            }
            if decision.matched
            else None
        ),
    }


__all__ = ["router"]
