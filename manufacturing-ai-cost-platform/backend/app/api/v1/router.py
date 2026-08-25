"""API v1 router.

Business routers are added here as they are implemented. Each must match
API_CONTRACT.yaml; endpoints are never invented in code
(AI_DEVELOPMENT_RULES.md sections 2, 18 and 34).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import system

api_router = APIRouter()
api_router.include_router(system.router)

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
