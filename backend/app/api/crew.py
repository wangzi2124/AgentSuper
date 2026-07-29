"""CrewAI multi-agent API endpoints."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.crew.crew_manager import CrewManager, TASK_CONFIGS

logger = logging.getLogger(__name__)

router = APIRouter()


class CrewTaskRequest(BaseModel):
    """Request schema for executing a CrewAI task."""
    task_type: str = Field(
        ...,
        description="Task type: 'research', 'analysis', 'orchestrated', or 'custom'",
        examples=["research"],
    )
    input: Dict[str, Any] = Field(
        ...,
        description="Input data. Keys depend on task_type: 'topic' for research, 'data_description' for analysis, 'query' for generic.",
        examples=[{"topic": "Latest advances in quantum computing"}],
    )
    custom_tasks: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="For task_type='custom': list of task specs with description, expected_output, agent_role, context_indices.",
    )
    timeout: float = Field(
        120.0,
        description="Maximum execution time in seconds",
        ge=10,
        le=600,
    )


class CrewTaskResponse(BaseModel):
    """Response schema for CrewAI task execution."""
    result: str
    metrics: Dict[str, Any]


class CrewStatusResponse(BaseModel):
    """Available task types and their configurations."""
    task_types: Dict[str, Dict[str, Any]]
    available_agents: List[str]
    bridged_tools_count: int


# Singleton manager
_crew_manager: Optional[CrewManager] = None


def _get_manager() -> CrewManager:
    global _crew_manager
    if _crew_manager is None:
        from app.runtime import get_app_state
        state = get_app_state()
        plugin_loader = getattr(state, "plugin_loader", None) if state else None
        skill_loader = getattr(state, "skill_loader", None) if state else None
        _crew_manager = CrewManager(
            plugin_loader=plugin_loader,
            skill_loader=skill_loader,
        )
    return _crew_manager


@router.get("/crew/status", response_model=CrewStatusResponse)
async def crew_status():
    """Get available task types, agents, and tool count."""
    manager = _get_manager()
    return CrewStatusResponse(
        task_types=TASK_CONFIGS,
        available_agents=list(TASK_CONFIGS.keys()),
        bridged_tools_count=len(manager._get_tools()),
    )


@router.post("/crew/run", response_model=CrewTaskResponse)
async def run_crew_task(request: CrewTaskRequest):
    """Execute a CrewAI multi-agent task.

    - **research**: Researcher gathers info, writer produces report
    - **analyst**: Analyst processes data, writer formats report
    - **orchestrated**: Coordinator manages specialist agents
    - **custom**: Define your own task chain via custom_tasks
    """
    manager = _get_manager()

    try:
        result = await asyncio.wait_for(
            manager.run(
                task_type=request.task_type,
                input_data=request.input,
                custom_tasks=request.custom_tasks,
            ),
            timeout=request.timeout,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Crew task timed out after {request.timeout}s",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Crew task failed")
        raise HTTPException(status_code=500, detail=f"Crew task failed: {e}")

    return CrewTaskResponse(result=result["result"], metrics=result["metrics"])


@router.post("/crew/refresh")
async def refresh_crew_tools():
    """Force refresh of bridged plugin tools."""
    manager = _get_manager()
    manager.refresh_tools()
    return {"status": "ok", "tools_count": len(manager._get_tools())}
