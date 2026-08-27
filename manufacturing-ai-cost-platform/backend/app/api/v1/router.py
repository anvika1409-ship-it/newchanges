"""API v1 router.

Business routers are added here as they are implemented. Each must match
API_CONTRACT.yaml; endpoints are never invented in code
(AI_DEVELOPMENT_RULES.md sections 2, 18 and 34).

Every router is imported unconditionally. A conditional include hides a broken
module behind a missing endpoint, which surfaces as a confusing 404 rather than
the import error that actually happened.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    ai_execution,
    anomalies,
    budgets,
    costs,
    forecasts,
    models,
    optimization,
    quality,
    system,
)

api_router = APIRouter()

api_router.include_router(system.router)
api_router.include_router(quality.router)
api_router.include_router(ai_execution.router)
api_router.include_router(costs.router)
api_router.include_router(budgets.router)
api_router.include_router(forecasts.router)
api_router.include_router(anomalies.router)
api_router.include_router(optimization.router)
api_router.include_router(models.router)

# Not yet implemented — each requires its own contract-conforming router:
#   /workloads /agents   Workloads
#   /plants /departments Organization
#   /policies            Policies
#   /governance/*        Governance
