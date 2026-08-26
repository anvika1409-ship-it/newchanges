"""API v1 router.

Business routers are added here as they are implemented. Each must match
API_CONTRACT.yaml; endpoints are never invented in code
(AI_DEVELOPMENT_RULES.md sections 2, 18 and 34).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import ai_execution, anomalies, forecasts, optimization, system

try:
    from app.api.v1.routes import models
    _HAS_MODELS = True
except ImportError:
    _HAS_MODELS = False

api_router = APIRouter()
api_router.include_router(system.router)
if _HAS_MODELS:
    api_router.include_router(models.router)
api_router.include_router(ai_execution.router)
api_router.include_router(forecasts.router)
api_router.include_router(anomalies.router)
api_router.include_router(optimization.router)

# Not yet implemented — each requires its own contract-conforming router:
#   /ai/execute          AI Execution
#   /cost/*              Costs
#   /budgets*            Budgets
#   /forecasts           Forecasts
#   /anomalies           Anomalies
#   /optimization/*      Optimization
#   /models*             Models
#   /workloads /agents   Workloads
#   /plants /departments Organization
#   /policies            Policies
#   /governance/*        Governance
