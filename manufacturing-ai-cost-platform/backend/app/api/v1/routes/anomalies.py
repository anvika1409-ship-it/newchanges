"""Anomaly endpoints.

Implements GET /anomalies matching API_CONTRACT.yaml.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.schemas.analytics import Anomaly, AnomalyList, PageInfo
from app.repositories.anomaly_repository import AnomalyRepository

try:
    from app.security.dependencies import get_current_principal
    _AUTH_DEPS = [Depends(get_current_principal)]
except ImportError:
    _AUTH_DEPS = []

router = APIRouter(tags=["Anomalies"])


@router.get(
    "/anomalies",
    summary="List detected anomalies",
    response_model=AnomalyList,
    dependencies=_AUTH_DEPS,
)
async def list_anomalies(
    request: Request,
    severity: Annotated[
        Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None,
        Query(description="Filter by anomaly severity"),
    ] = None,
    scope_type: Annotated[str | None, Query(description="Filter by scope type")] = None,
    scope_id: Annotated[str | None, Query(description="Filter by scope id")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AnomalyList:
    """Retrieve detected anomalies matching filter criteria."""
    database = request.app.state.database

    async with database.session() as session:
        repo = AnomalyRepository(session)
        items, total_count = await repo.list_anomalies(
            severity=severity,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
            offset=offset,
        )

        anomaly_items = [
            Anomaly(
                id=item.id,
                timestamp=item.timestamp,
                scope_type=item.scope_type,
                scope_id=item.scope_id,
                anomaly_type=item.anomaly_type,
                severity=item.severity,  # type: ignore
                expected_value=item.expected_value,
                actual_value=item.actual_value,
                deviation_percent=item.deviation_percent,
                reason=item.reason,
                status=item.status,
            )
            for item in items
        ]

        return AnomalyList(
            items=anomaly_items,
            page=PageInfo(total=total_count, limit=limit, offset=offset),
        )
