"""Optimization endpoints.

Implements GET /optimization/recommendations and POST /optimization/analyze
matching API_CONTRACT.yaml.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

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
    SimulationFigure,
    SimulationRequest,
    SimulationResult,
)
from app.db.models.optimization import OptimizationRecommendationRecord, OptimizationStatus
from app.optimization.engine import OptimizationEngine
from app.optimization.simulation import Baseline, SimulationInput, simulate
from app.optimization.simulation import ModelMixEntry as DomainMixEntry
from app.repositories.cost_repository import CostAggregationRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.optimization_repository import OptimizationRepository

# Imported unconditionally. A try/except around this import turned a
# protected endpoint into an open one whenever the import failed, which is a
# silent authentication bypass (SECURITY.md section 18,
# AI_DEVELOPMENT_RULES.md section 26).
from app.security.dependencies import get_current_principal
from app.security.scope import AuthorizedScope, ScopeConstraint

_AUTH_DEPS = [Depends(get_current_principal)]

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
    principal = getattr(request.state, "principal", None)
    tenant_id = getattr(principal, "tenant_id", "tenant-1") or "tenant-1"

    async with database.session() as session:
        repo = OptimizationRepository(session)
        record = OptimizationRecommendationRecord(
            id=rec_id,
            tenant_id=tenant_id,
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


# ── What-if simulation (AI_WORKFLOWS.md section 10) ────────────────────────
async def _load_baseline(session: Any, *, tenant_id: str, currency: str) -> Baseline:
    """Measured spend for this tenant, from recorded telemetry.

    Read through the aggregation repository so the scope filter and the
    actual/estimated split are applied exactly as the cost endpoints apply them,
    rather than reimplemented here.
    """
    scope = AuthorizedScope(
        tenant_id=tenant_id, branches=(ScopeConstraint(tenant_id=tenant_id),)
    )
    totals = await CostAggregationRepository(session).summary(scope)
    return Baseline(
        actual_cost=totals.actual_cost,
        estimated_cost=totals.estimated_cost,
        total_requests=totals.total_requests,
        currency=currency,
    )


async def _load_registry(session: Any, model_ids: list[str]) -> dict[str, Any]:
    """The registry entries named by the mix, keyed by id.

    A id that resolves to nothing is simply absent, and the simulator treats an
    absent model as unpriced rather than free.
    """
    repository = ModelRepository(session)
    found: dict[str, Any] = {}
    for model_id in dict.fromkeys(model_ids):
        entry = await repository.get_by_id(model_id)
        if entry is not None:
            found[model_id] = entry
    return found


@router.post(
    "/simulate",
    summary="Run a what-if simulation",
    response_model=SimulationResult,
    dependencies=_AUTH_DEPS,
)
async def simulate_optimization(
    request: Request,
    body: SimulationRequest,
) -> SimulationResult:
    """Compare current, forecast and optimized cost under given assumptions.

    Read-only. No policy is created, approved or activated and no model is
    invoked; the computation is arithmetic over recorded telemetry and registry
    pricing. Tenant comes from the authenticated principal, never the body
    (SECURITY.md section 5).
    """
    principal = request.state.principal
    database = request.app.state.database
    currency = request.app.state.settings.platform_base_currency

    async with database.session() as session:
        baseline = await _load_baseline(
            session, tenant_id=principal.tenant_id, currency=currency
        )
        registry = await _load_registry(
            session, [entry.model_id for entry in body.model_mix]
        )

    result = simulate(
        SimulationInput(
            request_volume=body.request_volume,
            production_volume=body.production_volume,
            image_volume=body.image_volume,
            budget_amount=body.budget_amount,
            model_mix=tuple(
                DomainMixEntry(model_id=e.model_id, share_percent=e.share_percent)
                for e in body.model_mix
            ),
            horizon_days=body.horizon_days,
            workload_id=body.workload_id,
        ),
        baseline,
        registry,
        # Baseline quality is not yet aggregated from telemetry, so quality
        # impact reports null rather than an invented "no change".
        baseline_quality=None,
    )

    def _figure(value: Any) -> SimulationFigure:
        return SimulationFigure(
            amount=value.amount,
            currency=value.currency,
            provenance=str(value.provenance),
        )

    return SimulationResult(
        horizon_days=result.horizon_days,
        current_cost=_figure(result.current_cost),
        forecast_cost=_figure(result.forecast_cost),
        optimized_cost=_figure(result.optimized_cost),
        estimated_saving=_figure(result.estimated_saving),
        estimated_saving_percent=result.estimated_saving_percent,
        quality_impact_percent=result.quality_impact_percent,
        risk_level=str(result.risk_level),
        within_budget=result.within_budget,
        unpriced_model_ids=list(result.unpriced_model_ids),
        assumptions=list(result.assumptions),
    )
