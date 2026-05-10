"""Top-level route aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from kernel.agents.access.routes.access import router as access_router
from kernel.agents.access.routes.gateways import router as gateways_router
from kernel.agents.access.routes.health import router as health_router
from kernel.agents.access.routes.session import router as session_router

router = APIRouter()
router.include_router(health_router)
router.include_router(access_router)
router.include_router(session_router)
router.include_router(gateways_router)
