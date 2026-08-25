"""System endpoints.

Implements ``/health`` and ``/ready`` exactly as declared in API_CONTRACT.yaml.
Both sit under the ``/api/v1`` server prefix the contract defines; no
unversioned alias is added, because that would be an undocumented endpoint
(AI_DEVELOPMENT_RULES.md sections 2 and 18).

These are the only two operations in the contract that carry no security
requirement and no response schema.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["System"])


class HealthResponse(BaseModel):
    status: Literal["alive"] = "alive"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]


@router.get("/health", summary="Health check", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe.

    Deliberately checks nothing external: a dependency outage must not cause a
    restart loop. Dependency state belongs to ``/ready``.
    """
    return HealthResponse()


@router.get(
    "/ready",
    summary="Readiness check",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Service is not ready"}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Readiness probe over the dependencies required to serve traffic.

    Checks the database and cache. The model gateway is **not** probed: doing so
    on every readiness check would add latency and, for a live provider, cost.
    Gateway configuration is validated at startup instead.
    """
    app_state: Any = request.app.state

    checks = {
        "database": await app_state.database.healthcheck(),
        "cache": await app_state.cache.healthcheck(),
    }

    all_ready = all(checks.values())
    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logger.warning("readiness_failed", extra={"checks": checks})

    return ReadinessResponse(
        status="ready" if all_ready else "not_ready",
        checks=checks,
    )
