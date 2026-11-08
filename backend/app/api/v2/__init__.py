from fastapi import APIRouter

from .agents import router as agents_router
from .config import router as config_router
from .local_models import router as local_models_router
from .mcp import router as mcp_router
from .network import router as network_router
from .references import router as references_router
from .skills_catalog import router as skills_catalog_router
from .policies import router as policies_router
from .session import router as session_router
from .websearch import router as websearch_router

router = APIRouter()
router.include_router(session_router)
router.include_router(agents_router)
router.include_router(config_router)
router.include_router(local_models_router)
router.include_router(mcp_router)
router.include_router(network_router)
router.include_router(references_router)
router.include_router(skills_catalog_router)
router.include_router(policies_router)
router.include_router(websearch_router)

__all__ = ["router"]
