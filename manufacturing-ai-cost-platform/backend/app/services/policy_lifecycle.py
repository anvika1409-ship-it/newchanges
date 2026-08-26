"""Policy Lifecycle Service.

Orchestrates the formal lifecycle:
Recommendation -> Policy Validation -> Risk Assessment -> Approval -> Version Creation -> Activation -> Audit -> Rollback

Enforces core governance rules (AI_DEVELOPMENT_RULES.md sections 8, 45; SECURITY.md section 14):
- Low-risk actions can be auto-approved only when configured.
- Medium/High/Critical actions strictly require authorized approval (FINOPS_MANAGER, ADMIN).
- Activation creates immutable new policy versions (old policies are preserved and marked SUPERSEDED).
- Every state change is audited.
- Rollback restores previous superseded versions.
- Applying unapproved recommendations is strictly rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.models.optimization import (
    OptimizationRecommendationRecord,
    OptimizationRiskLevel,
    OptimizationStatus,
)
from app.db.models.policy import PolicyStatus, RoutingPolicyRecord
from app.repositories.optimization_repository import OptimizationRepository
from app.repositories.policy_repository import PolicyRepository
from app.security.events import SecurityEvent, record_security_event
from app.security.principal import Principal, Role

logger = get_logger(__name__)


class PolicyConflictError(Exception):
    """Raised when an action violates policy lifecycle preconditions."""


class PolicyAuthorizationError(Exception):
    """Raised when the principal lacks permission to approve or apply policy."""


class PolicyLifecycleService:
    """Service managing the approval, versioning, activation, and rollback of policies."""

    def __init__(
        self,
        optimization_repository: OptimizationRepository,
        policy_repository: PolicyRepository,
        *,
        auto_approve_low_risk: bool = False,
    ) -> None:
        self._opt_repo = optimization_repository
        self._policy_repo = policy_repository
        self._auto_approve_low_risk = auto_approve_low_risk

    # ── 1. Policy Validation & Risk Assessment ────────────────────

    def validate_policy(self, recommendation: OptimizationRecommendationRecord) -> tuple[bool, str]:
        """Validate recommendation parameters prior to activation."""
        if not recommendation.recommended_strategy:
            return False, "Missing recommended strategy"
        if recommendation.estimated_saving < 0:
            return False, "Negative savings invalid"
        return True, "Valid"

    def assess_risk(self, recommendation: OptimizationRecommendationRecord) -> str:
        """Determine risk level of recommendation."""
        return recommendation.risk_level or OptimizationRiskLevel.LOW

    # ── 2. Approval Gating ────────────────────────────────────────

    async def approve_recommendation(
        self,
        recommendation_id: str,
        *,
        principal: Principal | None = None,
        approved: bool = True,
        reason: str = "",
        auto_approve_override: bool = False,
    ) -> OptimizationRecommendationRecord:
        """Approve or reject an optimization recommendation."""
        rec = await self._opt_repo.get_by_id(recommendation_id)
        if rec is None:
            raise ValueError(f"Recommendation '{recommendation_id}' not found")

        # 1. Evaluate risk and authorization requirement
        is_low_risk = rec.risk_level == OptimizationRiskLevel.LOW
        can_auto_approve = (self._auto_approve_low_risk or auto_approve_override) and is_low_risk

        if not can_auto_approve:
            if principal is None:
                raise PolicyAuthorizationError("Authentication required for policy approval")

            # Check if principal holds FINOPS_MANAGER or ADMIN
            has_approval_role = any(
                assignment.role in (Role.FINOPS_MANAGER, Role.ADMIN)
                for assignment in principal.assignments
            )
            if not has_approval_role:
                record_security_event(
                    SecurityEvent.AUTHORIZATION_DENIED,
                    reason="approval_role_required",
                    subject=principal.subject,
                    tenant_id=principal.tenant_id,
                    recommendation_id=recommendation_id,
                    risk_level=rec.risk_level,
                )
                raise PolicyAuthorizationError(
                    f"Principal '{principal.subject}' lacks FINOPS_MANAGER or ADMIN role to approve {rec.risk_level} risk policy"
                )

        now = datetime.now(UTC)
        approver_subject = principal.subject if principal else "system_auto_approver"

        if approved:
            rec.status = OptimizationStatus.APPROVED
            rec.approved_at = now
            rec.approved_by = approver_subject
            logger.info(
                "policy_recommendation_approved",
                extra={
                    "recommendation_id": recommendation_id,
                    "approved_by": approver_subject,
                    "risk_level": rec.risk_level,
                },
            )
        else:
            rec.status = OptimizationStatus.REJECTED
            rec.recommendation_reason = f"{rec.recommendation_reason} [REJECTED: {reason}]"
            logger.info(
                "policy_recommendation_rejected",
                extra={
                    "recommendation_id": recommendation_id,
                    "rejected_by": approver_subject,
                    "reason": reason,
                },
            )

        return rec

    # ── 3. Policy Versioning & Activation ─────────────────────────

    async def apply_policy(
        self,
        recommendation_id: str,
        *,
        principal: Principal | None = None,
        activation_mode: str = "FULL",
        canary_traffic_percent: float | None = None,
        reason: str = "",
    ) -> tuple[OptimizationRecommendationRecord, RoutingPolicyRecord]:
        """Activate an approved recommendation by creating a new immutable policy version."""
        rec = await self._opt_repo.get_by_id(recommendation_id)
        if rec is None:
            raise ValueError(f"Recommendation '{recommendation_id}' not found")

        # PRECONDITION: Must be APPROVED
        if rec.status != OptimizationStatus.APPROVED:
            raise PolicyConflictError(
                f"Cannot apply recommendation with status '{rec.status}'. Recommendation must be APPROVED first."
            )

        # PRECONDITION: Validate strategy
        is_valid, err_msg = self.validate_policy(rec)
        if not is_valid:
            raise PolicyConflictError(f"Policy validation failed: {err_msg}")

        # 1. Fetch existing active policy (if any) to supersede
        workload_type = rec.workload_id
        tenant_id = rec.tenant_id

        current_active = await self._policy_repo.get_active_policy(
            workload_type=workload_type, tenant_id=tenant_id
        )

        superseded_id: str | None = None
        if current_active is not None:
            superseded_id = current_active.id
            current_active.status = PolicyStatus.SUPERSEDED
            logger.info(
                "policy_superseded",
                extra={"superseded_policy_id": superseded_id, "workload_type": workload_type},
            )

        # 2. Increment version number (immutable versioning)
        latest_version = await self._policy_repo.get_latest_version_number(
            workload_type=workload_type, tenant_id=tenant_id
        )
        new_version = latest_version + 1

        now = datetime.now(UTC)
        creator = principal.subject if principal else "system"
        policy_status = PolicyStatus.CANARY if activation_mode.upper() == "CANARY" else PolicyStatus.ACTIVE

        # 3. Create new routing policy record
        new_policy = RoutingPolicyRecord(
            tenant_id=tenant_id,
            workload_type=workload_type,
            complexity="medium",
            business_priority="NORMAL",
            selected_model_id=None,
            version=new_version,
            status=policy_status,
            canary_traffic_percent=canary_traffic_percent if policy_status == PolicyStatus.CANARY else None,
            reason=reason or rec.recommendation_reason,
            created_by=creator,
            approved_by=rec.approved_by,
            activated_at=now,
        )
        await self._policy_repo.create(new_policy)

        # 4. Update recommendation record
        rec.status = OptimizationStatus.APPLIED
        rec.applied_policy_id = new_policy.id
        rec.superseded_policy_id = superseded_id
        rec.applied_at = now

        logger.info(
            "policy_activated",
            extra={
                "recommendation_id": recommendation_id,
                "applied_policy_id": new_policy.id,
                "version": new_version,
                "status": policy_status,
            },
        )

        return rec, new_policy

    # ── 4. Policy Rollback ────────────────────────────────────────

    async def rollback_policy(
        self,
        recommendation_id: str,
        *,
        principal: Principal | None = None,
        reason: str = "",
    ) -> tuple[OptimizationRecommendationRecord, RoutingPolicyRecord | None]:
        """Roll back an applied policy and reactivate the superseded version."""
        rec = await self._opt_repo.get_by_id(recommendation_id)
        if rec is None:
            raise ValueError(f"Recommendation '{recommendation_id}' not found")

        if rec.status != OptimizationStatus.APPLIED:
            raise PolicyConflictError(
                f"Cannot roll back recommendation with status '{rec.status}'. Must be APPLIED."
            )

        now = datetime.now(UTC)

        # 1. Mark applied policy as ROLLED_BACK
        if rec.applied_policy_id:
            applied_policy = await self._policy_repo.get_by_id(rec.applied_policy_id)
            if applied_policy:
                applied_policy.status = PolicyStatus.ROLLED_BACK

        # 2. Reactivate superseded policy if present
        reactivated_policy: RoutingPolicyRecord | None = None
        if rec.superseded_policy_id:
            superseded = await self._policy_repo.get_by_id(rec.superseded_policy_id)
            if superseded:
                superseded.status = PolicyStatus.ACTIVE
                reactivated_policy = superseded

        # 3. Update recommendation
        rec.status = OptimizationStatus.ROLLED_BACK
        rec.rolled_back_at = now

        logger.info(
            "policy_rolled_back",
            extra={
                "recommendation_id": recommendation_id,
                "rolled_back_policy_id": rec.applied_policy_id,
                "reactivated_policy_id": rec.superseded_policy_id,
                "reason": reason,
            },
        )

        return rec, reactivated_policy
