"""Unit and integration tests for the Policy Lifecycle.

Tests cover:
- Approval of pending recommendations
- Rejection of recommendations
- Unauthorized approval attempts (non-FinOps role rejected with 403)
- High-risk approval requirement (auto-approval blocked for high risk)
- Policy versioning (immutable versions created, old policy SUPERSEDED, not overwritten)
- Applying unapproved recommendation rejected (409 Conflict)
- Policy rollback (reverts active policy to ROLLED_BACK and reactivates superseded version)
- Full API routes (/approve, /apply, /rollback)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models.optimization import (
    OptimizationRecommendationRecord,
    OptimizationRiskLevel,
    OptimizationStatus,
)
from app.db.models.policy import PolicyStatus, RoutingPolicyRecord
from app.main import create_app
from app.repositories.optimization_repository import OptimizationRepository
from app.repositories.policy_repository import PolicyRepository
from app.security.identity import DevelopmentIdentityAdapter
from app.security.principal import Principal, Role, RoleAssignment, ScopeType
from app.services.policy_lifecycle import (
    PolicyAuthorizationError,
    PolicyConflictError,
    PolicyLifecycleService,
)

TENANT_1 = "tenant-1"


@pytest.fixture
def identity_adapter(settings: Settings) -> DevelopmentIdentityAdapter:
    return DevelopmentIdentityAdapter(settings)


@pytest.fixture
def finops_headers(identity_adapter: DevelopmentIdentityAdapter) -> dict[str, str]:
    token = identity_adapter.issue_token(
        subject="finops-lead",
        tenant_id=TENANT_1,
        assignments=(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_1),),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_headers(identity_adapter: DevelopmentIdentityAdapter) -> dict[str, str]:
    token = identity_adapter.issue_token(
        subject="viewer-user",
        tenant_id=TENANT_1,
        assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_1),),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def app_instance(settings: Settings):
    from app.db.models.control_plane import Department, Plant, Tenant, Workload

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with app.state.database.session() as session:
            t = Tenant(id=TENANT_1, name="Tenant 1", status="ACTIVE")
            p = Plant(id="plant-1", tenant_id=TENANT_1, name="Plant 1", status="ACTIVE")
            d = Department(id="dept-1", plant_id="plant-1", name="Dept 1", status="ACTIVE")
            wl = Workload(
                id="predictive_maintenance",
                plant_id="plant-1",
                department_id="dept-1",
                name="PM",
                workload_type="predictive_maintenance",
                status="ACTIVE",
            )
            session.add_all([t, p, d, wl])
            await session.commit()
        yield app


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


# ── Fixtures & Seed Helpers ───────────────────────────────────────


async def _seed_pending_recommendation(
    session, risk_level: str = OptimizationRiskLevel.LOW
) -> OptimizationRecommendationRecord:
    repo = OptimizationRepository(session)
    rec = OptimizationRecommendationRecord(
        id=f"rec-{risk_level.lower()}-001",
        tenant_id=TENANT_1,
        workload_id="predictive_maintenance",
        current_strategy="STATIC_PRIMARY (claude-3-5-sonnet)",
        recommended_strategy="Model Routing: Tiered Mini Routing",
        estimated_saving=400.0,
        estimated_saving_percent=40.0,
        quality_impact_percent=0.5,
        latency_impact_percent=-30.0,
        risk_level=risk_level,
        recommendation_reason="Route standard tasks to lightweight model",
        status=OptimizationStatus.PENDING_APPROVAL,
        created_at=datetime.now(UTC),
    )
    return await repo.create(rec)


async def _seed_active_policy(session, version: int = 1) -> RoutingPolicyRecord:
    repo = PolicyRepository(session)
    policy = RoutingPolicyRecord(
        id=f"pol-v{version}-001",
        tenant_id=TENANT_1,
        workload_type="predictive_maintenance",
        complexity="medium",
        business_priority="NORMAL",
        selected_model_id=None,
        version=version,
        status=PolicyStatus.ACTIVE,
        reason="Initial baseline policy",
        created_by="admin",
        activated_at=datetime.now(UTC),
    )
    return await repo.create(policy)


# ── Unit Tests: PolicyLifecycleService ─────────────────────────────


class TestPolicyLifecycleService:
    """Unit tests for the policy lifecycle service business logic."""

    async def test_approval_by_finops_manager(self, app_instance) -> None:
        """FINOPS_MANAGER can approve a pending recommendation."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            service = PolicyLifecycleService(opt_repo, pol_repo)

            rec = await _seed_pending_recommendation(session)

            principal = Principal(
                subject="finops-1",
                tenant_id=TENANT_1,
                assignments=(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_1),),
            )

            approved_rec = await service.approve_recommendation(rec.id, principal=principal)
            assert approved_rec.status == OptimizationStatus.APPROVED
            assert approved_rec.approved_by == "finops-1"
            assert approved_rec.approved_at is not None

    async def test_rejection_of_recommendation(self, app_instance) -> None:
        """Rejecting transitions status to REJECTED with note."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            service = PolicyLifecycleService(opt_repo, pol_repo)

            rec = await _seed_pending_recommendation(session)
            principal = Principal(
                subject="finops-1",
                tenant_id=TENANT_1,
                assignments=(RoleAssignment(Role.FINOPS_MANAGER, ScopeType.TENANT, TENANT_1),),
            )

            rejected_rec = await service.approve_recommendation(
                rec.id, principal=principal, approved=False, reason="Too aggressive on latency"
            )
            assert rejected_rec.status == OptimizationStatus.REJECTED
            assert "Too aggressive on latency" in rejected_rec.recommendation_reason

    async def test_unauthorized_approval_rejected(self, app_instance) -> None:
        """VIEWER role attempting approval raises PolicyAuthorizationError."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            service = PolicyLifecycleService(opt_repo, pol_repo)

            rec = await _seed_pending_recommendation(session, risk_level=OptimizationRiskLevel.MEDIUM)
            viewer_principal = Principal(
                subject="viewer-1",
                tenant_id=TENANT_1,
                assignments=(RoleAssignment(Role.VIEWER, ScopeType.TENANT, TENANT_1),),
            )

            with pytest.raises(PolicyAuthorizationError):
                await service.approve_recommendation(rec.id, principal=viewer_principal)

    async def test_high_risk_approval_required(self, app_instance) -> None:
        """HIGH risk action cannot be auto-approved even when auto_approve_low_risk=True."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            # Service configured to auto-approve low risk only
            service = PolicyLifecycleService(opt_repo, pol_repo, auto_approve_low_risk=True)

            high_risk_rec = await _seed_pending_recommendation(
                session, risk_level=OptimizationRiskLevel.HIGH
            )

            # Attempting without authorized principal must fail
            with pytest.raises(PolicyAuthorizationError):
                await service.approve_recommendation(high_risk_rec.id, principal=None)

    async def test_apply_unapproved_recommendation_fails(self, app_instance) -> None:
        """Applying a PENDING_APPROVAL recommendation raises PolicyConflictError."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            service = PolicyLifecycleService(opt_repo, pol_repo)

            unapproved_rec = await _seed_pending_recommendation(session)

            with pytest.raises(PolicyConflictError) as exc:
                await service.apply_policy(unapproved_rec.id)
            assert "must be APPROVED" in str(exc.value)

    async def test_policy_versioning_and_superseding(self, app_instance) -> None:
        """Applying creates version 2, marks version 1 SUPERSEDED, does NOT overwrite."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            service = PolicyLifecycleService(opt_repo, pol_repo)

            # Seed existing active policy v1
            active_v1 = await _seed_active_policy(session, version=1)

            # Seed & approve recommendation
            rec = await _seed_pending_recommendation(session)
            rec.status = OptimizationStatus.APPROVED

            applied_rec, new_policy = await service.apply_policy(
                rec.id, activation_mode="CANARY", canary_traffic_percent=20.0
            )

            # 1. New version is 2
            assert new_policy.version == 2
            assert new_policy.status == PolicyStatus.CANARY
            assert new_policy.canary_traffic_percent == 20.0

            # 2. Previous version 1 is preserved and marked SUPERSEDED
            old_policy = await pol_repo.get_by_id(active_v1.id)
            assert old_policy is not None
            assert old_policy.version == 1
            assert old_policy.status == PolicyStatus.SUPERSEDED

            # 3. Recommendation links both versions
            assert applied_rec.status == OptimizationStatus.APPLIED
            assert applied_rec.applied_policy_id == new_policy.id
            assert applied_rec.superseded_policy_id == active_v1.id

    async def test_policy_rollback(self, app_instance) -> None:
        """Rollback transitions active policy to ROLLED_BACK and reactivates superseded version."""
        async with app_instance.state.database.session() as session:
            opt_repo = OptimizationRepository(session)
            pol_repo = PolicyRepository(session)
            service = PolicyLifecycleService(opt_repo, pol_repo)

            # Setup: v1 superseded by v2
            active_v1 = await _seed_active_policy(session, version=1)
            rec = await _seed_pending_recommendation(session)
            rec.status = OptimizationStatus.APPROVED

            _, v2_policy = await service.apply_policy(rec.id, activation_mode="FULL")

            # Execute Rollback
            rolled_rec, reactivated_policy = await service.rollback_policy(
                rec.id, reason="Unexpected error rate spike"
            )

            assert rolled_rec.status == OptimizationStatus.ROLLED_BACK
            assert rolled_rec.rolled_back_at is not None

            # v2 is marked ROLLED_BACK
            v2_current = await pol_repo.get_by_id(v2_policy.id)
            assert v2_current.status == PolicyStatus.ROLLED_BACK

            # v1 is reactivated to ACTIVE
            assert reactivated_policy is not None
            assert reactivated_policy.id == active_v1.id
            assert reactivated_policy.status == PolicyStatus.ACTIVE


# ── Integration Tests: API Endpoints ──────────────────────────────


class TestPolicyLifecycleAPI:
    """API endpoint tests for POST /approve, /apply, and /rollback."""

    async def test_api_approve_success(
        self, client: AsyncClient, app_instance, finops_headers: dict[str, str]
    ) -> None:
        """POST /optimization/{id}/approve returns 200 with APPROVED status."""
        async with app_instance.state.database.session() as session:
            rec = await _seed_pending_recommendation(session)

        response = await client.post(
            f"/api/v1/optimization/{rec.id}/approve",
            # The contract's ApprovalDecision shape: an explicit decision, not a
            # boolean that defaults to approving.
            json={"decision": "APPROVED", "comments": "Approved for Q3 savings"},
            headers=finops_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "APPROVED"
        assert data["approved_by"] == "finops-lead"

    async def test_api_approve_unauthorized_forbidden(
        self, client: AsyncClient, app_instance, viewer_headers: dict[str, str]
    ) -> None:
        """POST /optimization/{id}/approve with VIEWER role returns 403 Forbidden."""
        async with app_instance.state.database.session() as session:
            rec = await _seed_pending_recommendation(session, risk_level=OptimizationRiskLevel.HIGH)

        response = await client.post(
            f"/api/v1/optimization/{rec.id}/approve",
            json={"decision": "APPROVED"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    async def test_api_apply_unapproved_returns_409(
        self, client: AsyncClient, app_instance, finops_headers: dict[str, str]
    ) -> None:
        """POST /optimization/{id}/apply on unapproved recommendation returns 409 Conflict."""
        async with app_instance.state.database.session() as session:
            rec = await _seed_pending_recommendation(session)

        response = await client.post(
            f"/api/v1/optimization/{rec.id}/apply",
            json={"activation_mode": "FULL"},
            headers=finops_headers,
        )
        assert response.status_code == 409

    async def test_api_apply_and_rollback_flow(
        self, client: AsyncClient, app_instance, finops_headers: dict[str, str]
    ) -> None:
        """End-to-end API lifecycle: Approve -> Apply -> Rollback."""
        async with app_instance.state.database.session() as session:
            await _seed_active_policy(session, version=1)
            rec = await _seed_pending_recommendation(session)

        # 1. Approve
        approve_resp = await client.post(
            f"/api/v1/optimization/{rec.id}/approve",
            json={"decision": "APPROVED"},
            headers=finops_headers,
        )
        assert approve_resp.status_code == 200

        # 2. Apply (Canary mode)
        apply_resp = await client.post(
            f"/api/v1/optimization/{rec.id}/apply",
            json={"activation_mode": "CANARY", "canary_traffic_percent": 15.0},
            headers=finops_headers,
        )
        assert apply_resp.status_code == 200
        apply_data = apply_resp.json()
        assert apply_data["status"] == "APPLIED"
        assert apply_data["applied_policy_version"] == 2
        assert apply_data["canary_traffic_percent"] == 15.0

        # 3. Rollback
        rollback_resp = await client.post(
            f"/api/v1/optimization/{rec.id}/rollback",
            json={"reason": "Canary rollback test"},
            headers=finops_headers,
        )
        assert rollback_resp.status_code == 200
        rollback_data = rollback_resp.json()
        assert rollback_data["status"] == "ROLLED_BACK"
        assert rollback_data["reactivated_policy_version"] == 1
