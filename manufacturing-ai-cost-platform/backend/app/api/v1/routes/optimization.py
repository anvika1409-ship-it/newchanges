"""Optimization endpoints.

Implements GET /optimization/recommendations and POST /optimization/analyze
matching API_CONTRACT.yaml.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.v1.schemas.analytics import PageInfo
from app.api.v1.schemas.optimization import (
    ApprovalDecision,
    OptimizationAnalyzeAccepted,
    OptimizationAnalyzeRequest,
    OptimizationApplyRequest,
    OptimizationApplyResult,
    OptimizationRecommendation,
    OptimizationRecommendationList,
    OptimizationRollbackRequest,
    OptimizationRollbackResult,
)
from app.db.models.optimization import OptimizationRecommendationRecord, OptimizationStatus
from app.optimization.engine import OptimizationEngine
from app.repositories.optimization_repository import OptimizationRepository

try:
    from app.security.dependencies import get_current_principal
    _AUTH_DEPS = [Depends(get_current_principal)]
except ImportError:
    _AUTH_DEPS = []

router = APIRouter(prefix="/optimization", tags=["Optimization"])


@router.get(
    "/recommendations",
    summary="List optimization recommendations",
    response_model=OptimizationRecommendationList,
    dependencies=_AUTH_DEPS,
)
async def list_recommendations(
    request: Request,
    status: Annotated[
        Literal["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "APPLIED", "ROLLED_BACK"] | None,
        Query(description="Filter by recommendation status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OptimizationRecommendationList:
    """Retrieve optimization recommendations matching filter criteria."""
    database = request.app.state.database

    async with database.session() as session:
        repo = OptimizationRepository(session)
        items, total_count = await repo.list_recommendations(
            status=status,
            limit=limit,
            offset=offset,
        )

        rec_items = [
            OptimizationRecommendation(
                id=item.id,
                workload_id=item.workload_id,
                current_strategy=item.current_strategy,
                recommended_strategy=item.recommended_strategy,
                estimated_saving=item.estimated_saving,
                estimated_saving_percent=item.estimated_saving_percent,
                quality_impact_percent=item.quality_impact_percent,
                latency_impact_percent=item.latency_impact_percent,
                risk_level=item.risk_level,  # type: ignore
                recommendation_reason=item.recommendation_reason,
                status=item.status,  # type: ignore
                provenance="ESTIMATED",
                applied_policy_id=item.applied_policy_id,
                superseded_policy_id=item.superseded_policy_id,
                created_at=item.created_at,
                approved_at=item.approved_at,
                applied_at=item.applied_at,
                rolled_back_at=item.rolled_back_at,
                approved_by=item.approved_by,
            )
            for item in items
        ]

        return OptimizationRecommendationList(
            items=rec_items,
            page=PageInfo(total=total_count, limit=limit, offset=offset),
        )


@router.post(
    "/analyze",
    summary="Generate an optimization analysis",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OptimizationAnalyzeAccepted,
    dependencies=_AUTH_DEPS,
)
async def analyze_optimization(
    request: Request,
    body: OptimizationAnalyzeRequest,
) -> OptimizationAnalyzeAccepted:
    """Trigger an optimization analysis and return 202 Accepted with recommendation ID."""
    database = request.app.state.database
    engine = OptimizationEngine()
    req_id = f"opt-req-{uuid.uuid4().hex[:8]}"

    analysis_result = engine.analyze(
        workload_id=body.workload_id,
        target_saving_percent=body.target_saving_percent,
    )

    rec_id = f"rec-{uuid.uuid4().hex[:8]}"

    async with database.session() as session:
        repo = OptimizationRepository(session)
        record = OptimizationRecommendationRecord(
            id=rec_id,
            workload_id=body.workload_id,
            current_strategy=analysis_result.current_strategy,
            recommended_strategy=analysis_result.recommended_strategy,
            estimated_saving=analysis_result.estimated_saving,
            estimated_saving_percent=analysis_result.estimated_saving_percent,
            quality_impact_percent=analysis_result.quality_impact,
            latency_impact_percent=analysis_result.latency_impact,
            risk_level=analysis_result.risk,
            recommendation_reason=analysis_result.reasoning,
            status=OptimizationStatus.PENDING_APPROVAL,
        )
        await repo.create(record)

    return OptimizationAnalyzeAccepted(
        request_id=req_id,
        recommendation_id=rec_id,
        status="PENDING_APPROVAL",
    )


@router.post(
    "/{id}/approve",
    summary="Approve optimization recommendation",
    response_model=OptimizationRecommendation,
    dependencies=_AUTH_DEPS,
)
async def approve_recommendation(
    request: Request,
    id: str,
    body: ApprovalDecision | None = None,
) -> OptimizationRecommendation:
    """Approve or reject a pending optimization recommendation."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    approved = body.approved if body else True
    reason = body.reason if body else ""

    from fastapi import HTTPException
    from app.repositories.policy_repository import PolicyRepository
    from app.services.policy_lifecycle import PolicyAuthorizationError, PolicyLifecycleService

    async with database.session() as session:
        opt_repo = OptimizationRepository(session)
        policy_repo = PolicyRepository(session)
        service = PolicyLifecycleService(opt_repo, policy_repo)

        try:
            rec = await service.approve_recommendation(
                id,
                principal=principal,
                approved=approved,
                reason=reason,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except PolicyAuthorizationError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

        return OptimizationRecommendation(
            id=rec.id,
            workload_id=rec.workload_id,
            current_strategy=rec.current_strategy,
            recommended_strategy=rec.recommended_strategy,
            estimated_saving=rec.estimated_saving,
            estimated_saving_percent=rec.estimated_saving_percent,
            quality_impact_percent=rec.quality_impact_percent,
            latency_impact_percent=rec.latency_impact_percent,
            risk_level=rec.risk_level,  # type: ignore
            recommendation_reason=rec.recommendation_reason,
            status=rec.status,  # type: ignore
            provenance="ESTIMATED",
            applied_policy_id=rec.applied_policy_id,
            superseded_policy_id=rec.superseded_policy_id,
            created_at=rec.created_at,
            approved_at=rec.approved_at,
            applied_at=rec.applied_at,
            rolled_back_at=rec.rolled_back_at,
            approved_by=rec.approved_by,
        )


@router.post(
    "/{id}/apply",
    summary="Apply approved optimization policy",
    response_model=OptimizationApplyResult,
    dependencies=_AUTH_DEPS,
)
async def apply_recommendation(
    request: Request,
    id: str,
    body: OptimizationApplyRequest | None = None,
) -> OptimizationApplyResult:
    """Create and activate a new immutable policy version for an approved recommendation."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    activation_mode = body.activation_mode if body else "CANARY"
    canary_pct = body.canary_traffic_percent if body else 10.0
    reason = body.reason if body else ""

    from fastapi import HTTPException
    from app.repositories.policy_repository import PolicyRepository
    from app.services.policy_lifecycle import PolicyConflictError, PolicyLifecycleService

    async with database.session() as session:
        opt_repo = OptimizationRepository(session)
        policy_repo = PolicyRepository(session)
        service = PolicyLifecycleService(opt_repo, policy_repo)

        try:
            rec, new_policy = await service.apply_policy(
                id,
                principal=principal,
                activation_mode=activation_mode,
                canary_traffic_percent=canary_pct,
                reason=reason,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except PolicyConflictError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        return OptimizationApplyResult(
            recommendation_id=rec.id,
            status="APPLIED",
            applied_policy_id=new_policy.id,
            applied_policy_version=new_policy.version,
            superseded_policy_id=rec.superseded_policy_id,
            activation_mode=activation_mode,
            canary_traffic_percent=canary_pct if activation_mode == "CANARY" else None,
        )


@router.post(
    "/{id}/rollback",
    summary="Roll back an applied optimization policy",
    response_model=OptimizationRollbackResult,
    dependencies=_AUTH_DEPS,
)
async def rollback_recommendation(
    request: Request,
    id: str,
    body: OptimizationRollbackRequest | None = None,
) -> OptimizationRollbackResult:
    """Roll back an applied policy version and reactivate the superseded policy."""
    database = request.app.state.database
    principal = getattr(request.state, "principal", None)
    reason = body.reason if body else ""

    from fastapi import HTTPException
    from app.repositories.policy_repository import PolicyRepository
    from app.services.policy_lifecycle import PolicyConflictError, PolicyLifecycleService

    async with database.session() as session:
        opt_repo = OptimizationRepository(session)
        policy_repo = PolicyRepository(session)
        service = PolicyLifecycleService(opt_repo, policy_repo)

        try:
            rec, reactivated = await service.rollback_policy(
                id,
                principal=principal,
                reason=reason,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except PolicyConflictError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        return OptimizationRollbackResult(
            recommendation_id=rec.id,
            status="ROLLED_BACK",
            rolled_back_policy_id=rec.applied_policy_id or "",
            reactivated_policy_id=reactivated.id if reactivated else None,
            reactivated_policy_version=reactivated.version if reactivated else None,
        )
